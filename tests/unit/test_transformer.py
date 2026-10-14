from cache_service.transformer import SimulatedTransformer


async def test_simulated_transformer_uppercases() -> None:
    assert await SimulatedTransformer(latency=0)("first string") == "FIRST STRING"
