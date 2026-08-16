# 发布与更新策略

普通 `push` 只是开发提交，不会自动成为用户更新。准备给用户使用时，在目标代码提交上创建一个版本 Tag，并修改根目录的 `update_manifest.json`：

```json
{
  "version": "0.3.0",
  "update_type": "normal",
  "target_ref": "v0.3.0",
  "min_version": "0.1.0",
  "message": "简短说明这次更新了什么"
}
```

发布流程：

1. 先完成代码、测试和文档，确认本地配置与记忆不会被代码更新覆盖。
2. 修改 `update_manifest.json`，提交后创建同名 Tag，例如 `v0.3.0`。
3. 将提交和 Tag 推送到远程：`git push origin main --tags`。
4. 用户控制台点击“检查更新”，确认版本类型后再点击“更新项目”。

版本类型：

- `hot`：配置和数据结构兼容，只更新代码并自动重启 Bot/Bridge，NapCat/QQ 不动。
- `normal`：普通功能更新，用户确认后更新，完成时询问是否重启 Bot/Bridge。
- `force`：安全问题或兼容性问题。设置 `min_version` 后，旧版本会被标记为必须更新。

更新程序会先创建 `backups/onebot-backup-*.zip`，备份 `.env.local`、模型预设、主题、Persona、表情词典和配置引用的 SQLite 文件，然后只执行稳定 Tag 的快进更新。工作区有本地代码改动时会停止，不会自动 stash、删除或覆盖文件。

`force` 只应该用于确实不能继续运行的版本，不要把普通功能更新标成强制更新。
