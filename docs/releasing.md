# 发布手册（Release Runbook）

本项目使用**语义化版本 + Keep a Changelog + GitHub Trusted Publishing（OIDC）**，
发布过程不需要在 GitHub 存放任何 PyPI API token。

## 发布前检查（本地）

```powershell
# 1) 全量测试与 lint
D:\Python\Python310\python.exe -m pytest
D:\Python\Python310\python.exe -m ruff check src tests

# 2) 构建并校验
D:\Python\Python310\python.exe -m build
D:\Python\Python310\python.exe -m twine check dist/*

# 3) 全新虚拟环境安装冒烟（验证入口点，而非用开发目录）
D:\Python\Python310\python.exe -m venv $env:TEMP\ndecon_release_venv
& "$env:TEMP\ndecon_release_venv\Scripts\python.exe" -m pip install (Get-ChildItem dist\*.whl).FullName
& "$env:TEMP\ndecon_release_venv\Scripts\ndecon.exe" version
```

## 首次发布：在 PyPI 配置 Trusted Publisher（只需一次）

PyPI 包名已核验可用：**novel-deconstruct**（CLI 入口仍为 `ndecon`）。

1. 登录 https://pypi.org → Account settings → Publishing → Add publisher：
   - PyPI Project Name：`novel-deconstruct`
   - Owner：`good-saltfish`
   - Repository name：`novel-deconstruct`
   - Workflow name：`release.yml`
   - Environment name：`pypi`
2. 该配置允许 `.github/workflows/release.yml` 在 `pypi` 环境中用 OIDC 免 token 发布。
3. PyPI 不允许同名项目发布后删除再重建；首次发布前确认名称拼写。

## 正式发布

1. 更新 `CHANGELOG.md`：把 `[Unreleased]` 改为 `[x.y.z] - YYYY-MM-DD`，并新建空的 Unreleased 段。
2. 更新 `pyproject.toml` 的 `version`（两处语义必须一致）。
3. 提交：`git commit -m "chore(release): vx.y.z"`。
4. 打标签并推送：`git tag vx.y.z && git push origin vx.y.z`。
   标签推送即触发 Release workflow：构建 → twine 校验 → 发布 PyPI → 创建 GitHub Release（自动生成 notes，附 sdist/wheel）。
5. 发布后验证：`pip install novel-deconstruct`、PyPI 项目页、GitHub Release 页面。

## 只演练不发布

GitHub 仓库 Actions 页手动运行 **Release** workflow，`publish` 保持不勾选：
只构建并上传 dist 制品，不接触 PyPI。

## 撤回与修复

- PyPI 不允许覆盖已发布版本，也不能删除文件后重用版本号；修复问题一律发新版本（yanked 仅限严重问题）。
- 若 release.yml 的 publish 步骤失败，GitHub Release 不会创建；修正后**删除远程与本地标签**，
  确认 PyPI 上未产生新版本，再重新打同一标签（仅在 PyPI 未发布成功时允许）。
