import asyncio

from licenselens.llm.classify import classify
from licenselens.llm.gateway import FakeLLM, LLMGateway


async def main():
    fake = FakeLLM()
    gateway = LLMGateway(fake, FakeLLM())

    label, result = await classify(
        gateway,
        "Classify the question.",
        "classify_v2",
        "Is my refund approved?",
    )

    print("Label:", label)
    print("Response:", result.content)
    print("Recorded request:", fake.seen)


asyncio.run(main())
