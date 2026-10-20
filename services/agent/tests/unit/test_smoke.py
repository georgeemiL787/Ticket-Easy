import agent_service


def test_package_imports() -> None:
    assert agent_service.__name__ == "agent_service"
