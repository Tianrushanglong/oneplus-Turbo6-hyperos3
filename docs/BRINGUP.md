# Bring-up 计划

本文件描述工程顺序，不是刷机教程。仓库出现明确标记的测试 Release 以前，不应对手机执行任何刷写。

## 0. 恢复能力门槛

开始前必须同时满足：

- 型号确认为 `PLU110`，而不是 Turbo 6V 或其他外观相似机型。
- Bootloader 已通过官方允许的流程解锁。
- 保存与当前系统版本一致的完整 ColorOS 16 全量包及 SHA-256。
- 已确认 bootloader 和 fastbootd 均可进入，电脑可稳定识别。
- 已备份个人数据；任何解锁、格式化或分区测试都可能清空数据。
- 读取并记录 anti-rollback 信息。未知时不降级、不跨版本刷固件。

## 1. 目标机清点

Root 用户先运行 `scripts/collect-rooted-layout.sh`。该脚本只生成布局报告；在报告经人工复核前，不允许批量导出分区。

从真机报告和原厂包提取：

- `ro.build.fingerprint`、SDK、VNDK、首发 API、补丁日期。
- kernel/GKI 版本、boot header、ramdisk 压缩格式。
- A/B 槽、虚拟 A/B、动态分区组和 super 总容量。
- `system`、`system_ext`、`product`、`vendor`、`odm` 及 dlkm 分区大小。
- device/framework compatibility matrix 与 manifest。
- AVB chain、每个 vbmeta descriptor、rollback index/location。

输出应成为可审核的 JSON 清单；任何容量或身份不确定都阻止打包。

## 2. donor 清点

已锁定 `onyx` Android 16 `OS3.0.303.0.WOLCNXM` Recovery 完整包，因为它与目标机同属 SM8735 平台。仍需确认：

- donor 确实是 full OTA，而不是增量包。
- `post-sdk-level=36`，版本标识属于 OS3。
- super 中 `mi_ext` 的存在、大小、依赖及挂载方式。
- framework compatibility matrix 对目标 `vendor` HAL 的要求。

## 3. 分区策略

第一阶段只考虑移植 framework 侧分区。目标机硬件和固件分区必须保留：

| 保留目标机 | 评估 donor 内容 |
| --- | --- |
| `boot`, `init_boot`, `vendor_boot`, `dtbo` | `system` |
| `vendor`, `odm`, `vendor_dlkm`, `odm_dlkm` | `system_ext` |
| modem/DSP/蓝牙/TEE/bootloader 固件 | `product` |
| 目标机 fstab、内核模块和硬件配置 | `mi_ext` |

`product`/`system_ext` 不能机械替换；其中可能包含硬件 overlay、权限 XML、init rc 和 Xiaomi 专属服务。最终归属由依赖扫描决定。

## 4. 首次开机适配

推荐按以下顺序收敛：

1. 先通过 lpmake dry-run 验证 super 分组和镜像尺寸。
2. 处理 VINTF 不兼容项，不通过全局关闭 VINTF 校验掩盖问题。
3. 合并目标机 init/fstab，移除 donor 固件刷写与硬件 init 项。
4. 修复属性冲突、framework overlay、权限 allowlist。
5. 从 enforcing 模式的 denial 日志逐条补最小 SELinux 规则；不发布 permissive 构建。
6. 修复 bootclasspath、apex、linker namespace 和 native library 缺失。

## 5. 测试顺序

每一阶段通过后再进入下一项：

1. 到达开机动画并能抓取 adb/logcat。
2. SystemUI 和设置向导可用，无持续崩溃。
3. SIM、通话、短信、移动数据和 IMS。
4. Wi-Fi、蓝牙、GPS、NFC。
5. 屏幕刷新率、亮度、触控、旋转和传感器。
6. 扬声器、麦克风、USB 音频。
7. 相机各镜头、闪光灯和视频编码。
8. 指纹、锁屏、Keystore/TEE。
9. 普通充电、快充、旁路供电、电池温度与关机充电。
10. 加密、重启、恢复出厂、双槽更新与长时间稳定性。

每次失败至少保留 `logcat -b all`、kernel log、pstore/ramoops（若可用）和对应构建清单。
