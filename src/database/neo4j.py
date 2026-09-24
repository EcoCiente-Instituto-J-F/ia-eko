from __future__ import annotations

from typing import Any

from neo4j import AsyncGraphDatabase, AsyncDriver

from src.core.config import Settings


class Neo4jDatabase:
    def __init__(self) -> None:
        self.client: AsyncDriver | None = None

    async def start(self, settings: Settings) -> None:
        if self.client is not None:
            return

        self.client = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(
                settings.neo4j_user,
                settings.neo4j_password,
            ),
        )

        await self.client.verify_connectivity()

    async def execute(
        self,
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:

        if self.client is None:
            raise RuntimeError(
                "Neo4j database not initialized"
            )

        async with self.client.session() as session:
            result = await session.run(
                query,
                parameters or {},
            )

            records = []

            async for record in result:
                records.append(
                    record.data()
                )

            return records

    async def ping(self) -> bool:
        if self.client is None:
            return False

        await self.client.verify_connectivity()

        return True

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()

        self.client = None


neo4j_db = Neo4jDatabase()