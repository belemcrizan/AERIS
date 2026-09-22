"""Enterprise support case: CONTROL vs AERIS, offline by default.

Offline (default, no key, deterministic scripted model):

    python examples/live_support_experiment.py
    python examples/live_support_experiment.py --flight status_timeout

Live (real model, costs money, see LIVE_AGENT.md):

    AERIS_LIVE=1 OPENAI_API_KEY=... python examples/live_support_experiment.py --live --seeds 30

Results go to ``results/`` (gitignored). The live run refuses to start
without both environment variables.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aeris.adapters.llm import OpenAIChatModel, live_enabled  # noqa: E402
from aeris.cases.support.campaign import campaign  # noqa: E402
from aeris.cases.support.scripted_model import ScriptedSupportModel  # noqa: E402
from aeris.core.enums import ExperimentArm  # noqa: E402
from aeris.evaluation.pairing import classify_intervention  # noqa: E402
from aeris.experiments.report import render_flight_report, write_results  # noqa: E402
from aeris.experiments.support import (  # noqa: E402
    DEFAULT_ARMS,
    ModelFactory,
    calibrate,
    run_campaign,
    run_trial,
)
from aeris.experiments.trace import format_trace  # noqa: E402


def model_factory(live: bool) -> ModelFactory:
    if not live:
        return lambda seed: ScriptedSupportModel(seed=seed)
    if not live_enabled():
        raise SystemExit("--live needs AERIS_LIVE=1 and OPENAI_API_KEY; refusing to run.")
    return lambda seed: OpenAIChatModel.from_env()


async def single_flight(args: argparse.Namespace, factory: ModelFactory, temperature: float) -> None:
    scenario = campaign()[args.flight]
    store, _ = await calibrate(factory, temperature=temperature)
    control = await run_trial(
        scenario, ExperimentArm.CONTROL, seed=args.seed, model_factory=factory, baselines=store,
        temperature=temperature,
    )
    aeris = await run_trial(
        scenario, ExperimentArm.AERIS_FULL, seed=args.seed, model_factory=factory, baselines=store,
        temperature=temperature,
    )
    label, note = classify_intervention(control.outcome, aeris.outcome)
    aeris.record.intervention_outcome = label
    aeris.record.attribution_note = note
    print(render_flight_report(control, aeris, scenario=scenario.scenario_id))
    print()
    print("AERIS trace")
    print("-----------")
    print(format_trace(aeris.timeline, route_names=aeris.route_names, include_telemetry=not args.quiet))


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="use the real model (needs AERIS_LIVE=1 + key)")
    parser.add_argument("--seeds", type=int, default=1, help="paired trials per scenario")
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--scenarios", nargs="*", help="scenario ids (default: full campaign)")
    parser.add_argument("--arms", nargs="*", choices=[arm.value for arm in DEFAULT_ARMS])
    parser.add_argument("--out", default="results", help="output directory")
    parser.add_argument("--flight", help="run one CONTROL/AERIS pair and print its report and trace")
    parser.add_argument("--seed", type=int, default=0, help="seed for --flight")
    parser.add_argument("--quiet", action="store_true", help="omit telemetry lines from traces")
    parser.add_argument("--no-context", action="store_true", help="skip calibration (static radar only)")
    args = parser.parse_args()

    factory = model_factory(args.live)
    temperature = float(os.getenv("AERIS_LIVE_TEMPERATURE", "0.7")) if args.live else 0.0
    if args.flight:
        await single_flight(args, factory, temperature)
        return

    arms = [ExperimentArm(value) for value in args.arms] if args.arms else list(DEFAULT_ARMS)
    seeds = list(range(args.seed_start, args.seed_start + args.seeds))
    result = await run_campaign(
        factory,
        scenario_ids=args.scenarios,
        arms=arms,
        seeds=seeds,
        temperature=temperature,
        contextual=not args.no_context,
    )
    paths = write_results(result, args.out)
    print(Path(paths["report"]).read_text(encoding="utf-8").split("## Interpretation")[0])
    for name, path in paths.items():
        print(f"{name:10} {path}")


if __name__ == "__main__":
    asyncio.run(main())
