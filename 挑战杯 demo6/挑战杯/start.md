原因已经定位：卡住的是**前端构建**，不是模型。

**为什么卡很久**

`restart_server.py` 每次启动都会无条件执行：

```text
1. pip install -r requirements.txt
2. node node_modules/typescript/bin/tsc -b
3. node node_modules/vite/bin/vite.js build
```

你看到的界面停在：

```text
使用 Node: C:\Users\uE.cache\...\node.exe
```

之后就是 `tsc -b` 和 `vite build` 在跑。即使你前端代码一行没改，它也会重新全量构建一次。你的机器如果开着杀毒软件或磁盘较慢，这里可能卡一两分钟甚至更久。

**我已经加了跳过参数**

现在可以这样启动：

快速启动，不装依赖、不重建前端：

```powershell
python .\restart_server.py --skip-deps --skip-build
```

后端代码改了，但前端没改：

```powershell
python .\restart_server.py --skip-deps --skip-build
```

前端 TS 改了，想只跑 Vite、跳过 tsc 类型检查：

```powershell
python .\restart_server.py --skip-deps --skip-tsc
```

完整构建一次：

```powershell
python .\restart_server.py --skip-deps
```

参数说明：

```text
--skip-deps   跳过 pip 依赖安装
--skip-build  跳过 tsc + vite，直接使用现有 dist
--skip-tsc    跳过 tsc，只执行 vite build
```

**建议日常启动方式**

第一次完整构建一次后，以后正常启动：

```powershell
python .\restart_server.py --skip-deps --skip-build
```

这样不会每次都重新编译前端，启动会快很多。

注意：如果你改了 `frontend/src/` 下的代码，记得用 `--skip-tsc` 或完整构建，否则页面会一直是旧版本。