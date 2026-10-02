# GitHub 上传说明

本目录是已经脱敏过的公开候选版。正式生产版本和本仓库必须长期分开维护。**不要把生产目录直接绑定 GitHub，也不要把旧 `.git` 目录复制进来。**

## 推荐仓库

- GitHub 仓库名：`document-review-workbench`
- 公开级别：基本完整源码公开
- 建议可见性：Public（仅在确认源码权属允许公开后）
- 建议版本标记：首次发布后创建 `v19.15` Release/Tag；以后仓库名保持不变，不要每个版本新建一个仓库。

## 应当上传

server.py、start.py、workbench/、web/、tests/、启动脚本、requirements-dev.txt、README/PUBLIC_RELEASE/SECURITY、tools/public_release_check.py

## 绝对不要上传

任何真实 Word/PDF/Excel；核查项目输出；日志；临时目录；客户/发行人/证券公司真实材料；旧 .git；本机路径；WPS/Word 临时文件

## 首次上传前

在仓库根目录执行：

```bash
python tools/public_release_check.py
python -m pytest -q
```

只有发布扫描通过、测试/冒烟检查达到 README 中基线后再上传。

## 推荐上传方式（Mac）

先在 GitHub 网页新建一个**空仓库**：不要勾选“Add a README”、`.gitignore` 或 License，因为本目录已经带 README 和 `.gitignore`。然后在本目录执行：

```bash
git init
git branch -M main
git add .
git status
git commit -m "Initial public release"
git remote add origin https://github.com/<你的GitHub用户名>/document-review-workbench.git
git push -u origin main
```

执行 `git add .` 后一定先看 `git status`。如果出现任何真实业务文件、数据库、日志、输出结果或你不认识的大文件，**不要 commit**，先停止上传。

如果你使用 GitHub Desktop，也必须选择这个已经脱敏的目录作为本地仓库，不能选择原生产目录。

## 不建议的上传方式

不要把 ZIP 本身直接上传到仓库作为主要内容。应该解压后把源码作为仓库内容提交。ZIP 可以以后作为 GitHub Release 的附件，但不是源码仓库本体。

不要从原开发目录执行 `git init`。不要把旧 `.git` 复制过来。不要通过“先上传，发现敏感文件后再删除”的方式试错，因为 Git 历史仍可能保留文件。

## 后续更新

每次从生产版本吸收新功能时，先复制需要的源码改动到本公开仓库，重新脱敏并运行发布检查，然后：

```bash
git status
git diff
python tools/public_release_check.py
git add .
git status
git commit -m "Update public release"
git push
```

不要用整目录覆盖的方式把生产版同步到公开仓库。

## License

如果确认你拥有对外许可权，并希望别人可以合法复制、修改和再分发，建议添加 MIT License。**Public 仓库本身不等于开源许可。** 如果权属不确定，先保持无 License，待确认后再添加。

## 本项目额外注意

这是最适合作为主展示仓库的项目。公开测试代码有价值，不要为了“干净”删除 tests。真实文档只在本机运行时使用，绝不提交。
