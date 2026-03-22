# usage

### pytest

PYTHONPATH=. pytest

### deploy

cd deploy/
./setup.sh

运行时常见排错命令：
查看运行状态：sudo systemctl status yachiyo.service
查看项目输出的日志：sudo journalctl -u yachiyo.service -f
查看自动拉取代码/更新的记录：tail -f deploy/update.log (该文件会在发生首次自动检查后生成)