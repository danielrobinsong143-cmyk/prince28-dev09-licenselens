import asyncio

from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from licenselens.config import get_settings
from licenselens.db.models import Document
from licenselens.db.session import make_engine, make_sessionmaker


async def main():
    engine = make_engine(get_settings())
    sessionmaker = make_sessionmaker(engine)

    try:
        async with sessionmaker() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            stmt = (
                select(Document)
                .options(selectinload(Document.pages))
                .where(Document.doc_type != "record")
                .where(~Document.metadata_.has_key("doc_kind"))
                .order_by(Document.id)
                .limit(5)
            )

            documents = list(await session.scalars(stmt))

            for doc in documents:
                text_length = sum(len(page.text) for page in doc.pages)

                print(f"\nID: {doc.external_id}")
                print(f"Title: {doc.title}")
                print(f"Pages: {len(doc.pages)}")
                print(f"Text characters: {text_length}")

    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
