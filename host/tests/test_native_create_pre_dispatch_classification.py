from controlstudio_host.native_projects import (
    _native_create_failed_before_confirmation,
)


def test_build_bundle_rejection_is_terminal_non_mutation():
    assert _native_create_failed_before_confirmation(
        "project.create_native was rejected by MCP: "
        "NATIVE_BUILD_BUNDLE_PLAN_NOT_READY: semantic authority is invalid"
    )


def test_unknown_post_dispatch_failure_remains_uncertain():
    assert not _native_create_failed_before_confirmation(
        "project.create_native was rejected by MCP: XG5000_SAVE_FAILED"
    )
