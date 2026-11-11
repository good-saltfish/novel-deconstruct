"""面板访问校验：token 为空保持旧行为，token 非空拦截一切非公开接口。

为什么要有这个文件：面板原本对本机任意进程开放。端口写在 `engine.log` 与
`last-exit.json` 里，任何本地进程读到端口就能读书稿正文、读任意本地文件、
删项目。加了令牌之后必须钉死三件事，否则会悄悄退化成两种坏状态：

1. 令牌设了却没生效（漏一处路由 = 后门还在）；
2. 令牌把正经客户端也挡了（插件每次请求都 401，界面看起来像「引擎没起来」）；
3. 公开面被一起挡住（`/api/health` 挂掉，插件探活直接超时）。

放行清单是有意的：`/api/health` 给插件探活，静态资源给浏览器加载页面本身。
这两块不含用户数据，挡住的代价却是整个插件不可用。
"""

import json
import urllib.error
import urllib.request

import pytest

from ndecon.panel.server import api_request, serve_in_thread

TOKEN = "t" * 48


@pytest.fixture()
def open_server(tmp_path):
    """没设令牌：保持向后兼容，浏览器直接打开仍然可用。"""
    srv, _thread = serve_in_thread(tmp_path, port=0)
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def guarded_server(tmp_path):
    """设了令牌：模拟插件拉起引擎的真实形态。"""
    srv, _thread = serve_in_thread(tmp_path, port=0, token=TOKEN)
    yield srv
    srv.shutdown()
    srv.server_close()


def raw_request(server, path, *, method="GET", headers=None, payload=None):
    """绕过 api_request 发裸请求 —— 专门用来看「不带令牌会怎样」。"""
    host, port = server.server_address[0], server.server_address[1]
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"http://{host}:{port}{path}", data=data, headers=headers or {}, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def test_no_token_keeps_old_behaviour(open_server) -> None:
    """不设令牌时一切照旧：命令行 `ndecon panel` 起的面板不能被锁死。"""
    assert api_request(open_server, "GET", "/api/projects") == (200, [])
    assert raw_request(open_server, "/api/projects") == (200, [])


def test_token_blocks_unauthenticated(guarded_server) -> None:
    """设了令牌，不带头的请求必须 401，而不是照常返回数据。"""
    status, body = raw_request(guarded_server, "/api/projects")
    assert status == 401
    assert "令牌" in body["error"]


def test_token_accepts_header(guarded_server) -> None:
    """带正确令牌头时正常放行 —— 插件走的就是这条路。"""
    status, body = raw_request(
        guarded_server, "/api/projects", headers={"X-Ndecon-Token": TOKEN}
    )
    assert (status, body) == (200, [])


def test_token_accepts_bearer(guarded_server) -> None:
    """`Authorization: Bearer` 也认：给将来接标准客户端留的口子。"""
    status, body = raw_request(
        guarded_server, "/api/projects", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert (status, body) == (200, [])


def test_wrong_token_rejected(guarded_server) -> None:
    """错令牌与没令牌一样处理，不能因为「格式对」就放行。"""
    assert raw_request(guarded_server, "/api/projects", headers={"X-Ndecon-Token": "x"})[0] == 401


def test_health_is_public(guarded_server) -> None:
    """探活必须免鉴权：插件在拿到令牌之前就要打它。"""
    assert raw_request(guarded_server, "/api/health") == (200, {"ok": True})


def test_static_assets_are_public(guarded_server) -> None:
    """页面本身要能加载，否则浏览器场景连 JS 都跑不起来。"""
    host, port = guarded_server.server_address[0], guarded_server.server_address[1]
    for path in ("/", "/index.html", "/static/app.js"):
        with urllib.request.urlopen(f"http://{host}:{port}{path}", timeout=10) as response:
            assert response.status == 200
            assert response.read()


def test_write_endpoints_are_guarded(guarded_server) -> None:
    """写接口同样受保护：漏掉 DELETE 就等于「谁都能删书稿」。"""
    assert raw_request(guarded_server, "/api/projects/whatever", method="DELETE")[0] == 401
    assert raw_request(
        guarded_server, "/api/projects/import", method="POST", payload={"file_path": "x"}
    )[0] == 401


def test_guarded_token_survives_exception_handling(tmp_path) -> None:
    """401 必须直接写响应，不能被 _dispatch 的兜底包成 500。

    这两件事在界面上完全不同：401 说明「你没权限」，500 说明「服务端炸了」。
    混成一个会让排查方向整个错掉 —— 所以单独钉一条，并顺带确认 token
    真的挂到了 server 上（`api_request` 靠它自动带头）。
    """
    srv, _thread = serve_in_thread(tmp_path, port=0, token=TOKEN)
    try:
        assert srv.panel_token == TOKEN
        status, body = raw_request(srv, "/api/projects")
        assert status == 401
        assert "服务端内部错误" not in body["error"]
        # api_request 自动带头，因此同一个服务器在测试里仍然可用
        assert api_request(srv, "GET", "/api/projects") == (200, [])
    finally:
        srv.shutdown()
        srv.server_close()
