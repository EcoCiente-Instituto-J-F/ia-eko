from __future__ import annotations

from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase

from src.core.config import Settings


class Neo4jDatabase:

    def __init__(self) -> None:
        self.client: AsyncDriver | None = None
        self.database: str | None = None


    async def start(
        self,
        settings: Settings,
    ) -> None:

        if self.client is not None:
            return

        self.client = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(
                settings.neo4j_username,
                settings.neo4j_password,
            ),
        )

        self.database = settings.neo4j_database

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

        async with self.client.session(
            database=self.database,
        ) as session:

            result = await session.run(
                query,
                parameters or {},
            )

            return [
                record.data()
                async for record in result
            ]


    async def ping(self) -> bool:

        if self.client is None:
            return False

        await self.client.verify_connectivity()

        return True


    async def close(self) -> None:

        if self.client is not None:
            await self.client.close()

        self.client = None
        self.database = None


neo4j_db = Neo4jDatabase()