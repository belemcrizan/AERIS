"""CONTROL vs AERIS evaluation. Prints metrics; does not accept the hypothesis."""

from __future__ import annotations

import asyncio
import json

from aeris.evaluation.harness import run_experiment


async def main() -> None:
    report = await run_experiment(repeats=1)
    print(
        json.dumps(
            {
                "hypothesis": report.hypothesis,
                "notes": report.notes,
                "control": report.control.model_dump(),
                "aeris": report.aeris.model_dump(),
                "delta_recovery_rate": report.delta_recovery_rate,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
