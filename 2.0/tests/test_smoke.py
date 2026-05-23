"""Smoke test for the empty package."""

import agora


def test_version() -> None:
    assert agora.__version__ == "0.0.1"


def test_subpackages_importable() -> None:
    import agora.apps
    import agora.apps.propfirm
    import agora.platform
    import agora.platform.control_plane
    import agora.platform.llm
    import agora.platform.memory
    import agora.platform.observability
    import agora.platform.shared
    import agora.platform.tools
    import agora.platform.workers

    assert all(
        [
            agora.platform,
            agora.platform.control_plane,
            agora.platform.workers,
            agora.platform.tools,
            agora.platform.memory,
            agora.platform.llm,
            agora.platform.observability,
            agora.platform.shared,
            agora.apps,
            agora.apps.propfirm,
        ]
    )
