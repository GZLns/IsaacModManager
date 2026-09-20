# certs/

这里放 **CA 证书包**（`ca_bundle.pem`）—— 只给命令行下载器 `tools/ws_download.py` 使用。

它的作用是把 Steam 的 HTTPS 证书链补全：合并 `certifi` 与 **本机 Windows 根证书存储区**，
解决 `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`。

生成方式（需要时自己跑一次）：

```powershell
python tools/make_ca_bundle.py
```

生成的 `*.pem` **不纳入版本管理**（各机器的系统根证书不一样，也不该随仓库分发）。

> 程序主体（界面里的工坊下载）**不需要**这个文件 —— 那部分的下载由 Steam 客户端完成。
