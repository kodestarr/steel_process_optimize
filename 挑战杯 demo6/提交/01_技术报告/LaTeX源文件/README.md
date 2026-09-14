# 技术方案报告 LaTeX 源文件

## 文件说明

- `main.tex`：报告正文。
- `report_data.tex`：封面信息与正式实验指标。
- `assets/`：程序结果图、Web 系统截图及对应实验摘要。
- `build-report.ps1`：Windows PowerShell 构建脚本。
- `compile.bat`：Windows 双击构建入口。

## 编译方法

在提交包根目录按 `Ctrl+Shift+B`，或在本目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build-report.ps1
```

构建脚本使用 XeLaTeX 连续编译两次，以生成正确的目录、交叉引用和最终 PDF。
