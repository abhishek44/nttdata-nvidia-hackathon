from recallzero.intelligence import NIMClient


def test_nim_configuration_accepts_key_or_local_endpoint() -> None:
    hosted = NIMClient(api_key="nvapi-test", base_url="https://integrate.api.nvidia.com/v1")
    missing_key = NIMClient(api_key=None, base_url="https://integrate.api.nvidia.com/v1")
    local = NIMClient(api_key=None, base_url="http://127.0.0.1:8000/v1")
    lan = NIMClient(api_key=None, base_url="http://192.168.1.20:8000/v1")

    assert hosted.configured is True
    assert missing_key.configured is False
    assert local.configured is True
    assert lan.configured is True
