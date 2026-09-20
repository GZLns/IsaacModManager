# -*- coding: utf-8 -*-
"""
登录 CLI —— 供管理器界面调用（全程无控制台窗口）

stdin 收一条 JSON，stdout 出一条 JSON。
密码只从 stdin 进来：不进命令行参数（进程列表看不到）、不写环境变量、不落文件。

支持的动作:
    {"action":"begin", "user":"...", "password":"..."}   提交账号密码
    {"action":"code",  "code":"1234"}                    提交验证码 / 手机确认
    {"action":"poll"}                                     仅轮询(手机点批准的情况)
    {"action":"status"}                                   查询登录状态
    {"action":"logout"}                                   退出登录(删除本地凭据)

返回:
    {"status":"ok",    "account":"..."}              登录成功（凭据已保存）
    {"status":"code",  "mode":"device|email", ...}   需要验证码
    {"status":"confirm_wait", ...}                   需要在手机上批准
    {"status":"error", "error":"..."}                失败

说明: 这里**不用** steam / steamctl 那个老库 —— 它的密码是明文发的，
现代 Steam 会拒绝（正确密码也报 InvalidPassword）。详见 steam_auth.py 顶部注释。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import steam_auth  # noqa: E402


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    raw = sys.stdin.read()
    try:
        p = json.loads(raw or "{}")
    except ValueError:
        emit({"status": "error", "error": "登录参数解析失败"})
        return 2

    action = (p.get("action") or "").strip()
    code = (p.get("code") or "").strip()
    mode = (p.get("mode") or "").strip()

    # 兼容: 没写 action 时按「有验证码就提交, 否则开始登录」推断
    if not action:
        action = "code" if (code or mode in ("confirm", "poll")) else "begin"

    try:
        if action == "status":
            who = steam_auth.logged_in_account()
            emit({"status": "ok" if who else "none", "logged_in": bool(who),
                  "account": who})
            return 0

        if action == "logout":
            steam_auth.clear_session()
            emit({"status": "ok", "logged_in": False})
            return 0

        if action == "begin":
            user = (p.get("user") or "").strip()
            password = p.get("password") or ""
            emit(steam_auth.begin_login(user, password))
            return 0

        if action == "code":
            if mode == "confirm" and not code:
                emit(steam_auth.poll_only())
            else:
                emit(steam_auth.submit_code(code))
            return 0

        if action == "poll":
            emit(steam_auth.poll_only())
            return 0

        if action == "token":
            emit({"status": "ok", "token": steam_auth.access_token()})
            return 0

        emit({"status": "error", "error": "未知动作: " + action})
        return 2
    except Exception as exc:                       # 兜底: 任何异常都要变成 JSON
        emit({"status": "error", "error": "%s: %s" % (type(exc).__name__, str(exc)[:180])})
        return 1


if __name__ == "__main__":
    sys.exit(main())
