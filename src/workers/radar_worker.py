"""Competitor Radar + Supplier Quality periodic worker."""
from __future__ import annotations

import asyncio

from loguru import logger

from src.core.database import AsyncSessionLocal
from src.core.services.competitor_radar_service import competitor_radar_service
from src.core.services.supplier_quality_service import supplier_quality_service


async def run_radar_cycle():
    async with AsyncSessionLocal() as session:
        result = await competitor_radar_service.scan_all(session)
        logger.info(
            f"Radar cycle: sources={result.get('sources')} "
            f"snaps={result.get('snapshots')} alerts={len(result.get('alerts') or [])}"
        )


async def run_quality_cycle():
    async with AsyncSessionLocal() as session:
        board = await supplier_quality_service.compute_and_apply(session)
        demoted = [r for r in board if r.get("demoted")]
        logger.info(f"Supplier quality: {len(board)} suppliers, demoted={len(demoted)}")


async def main():
    logger.info("Radar/Quality worker started (every 10 min)")
    loop = 0
    while True:
        try:
            loop += 1
            await run_radar_cycle()
            # Quality every other cycle (~20 min)
            if loop % 2 == 0:
                await run_quality_cycle()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Radar worker error: {e}")
        await asyncio.sleep(600)


if __name__ == "__main__":
    asyncio.run(main())
