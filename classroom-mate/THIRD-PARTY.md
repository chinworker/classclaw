# 第三方组件

- .NET 10 / Windows Desktop runtime：微软，MIT（部分随附第三方组件有独立声明）；发布目录保留其 LICENSE/ThirdPartyNotices。
- NAudio 2.2.1：MIT，https://github.com/naudio/NAudio 。
- System.Speech / ProtectedData 10.0.0：微软，MIT，https://github.com/dotnet/runtime 。
- CPython 3.13.15 Windows embeddable：Python Software Foundation License；runtime/LICENSE.txt 随包保留。
- aiortc 1.15.0：BSD-3-Clause；PyAV 17.1.0：BSD-3-Clause；对应 wheel 的 dist-info/licenses 随包保留。
- FFmpeg 及其编解码依赖由 PyAV 官方 Windows wheel 提供；其 LGPL/GPL/其他许可与构建信息以所分发 wheel 内附许可及 PyAV 官方构建资料为准。对外再分发或修改原生库时须同时满足相关许可证的源码、声明和重新链接等义务，不因本项目 Python 封装而免除。
- 其他 Python 依赖的固定版本与 wheel SHA-256 见 media/requirements-lock.txt；完整 METADATA、许可证及 dist-info 保留于 runtime/Lib/site-packages。

发布包未做商业代码签名。不得删掉运行时或 wheel 自带许可证后再分发。
