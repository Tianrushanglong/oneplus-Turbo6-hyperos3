# HyperOS 3 port for OnePlus Turbo 6 (PLU110)

> **状态：Bring-up / 尚无可刷入版本。** 当前仓库只包含信息采集、输入校验和构建基础设施。不要把 CI 中的测试文件刷入手机。

本项目用于把 Android 16 版 HyperOS 3 移植到国行 OnePlus Turbo 6（`PLU110`）。移植脚本会尽量保留目标机的内核、固件、`vendor`/`odm` 和硬件相关分区，并只从 donor 包中取需要适配的 HyperOS framework 分区。

## 已确定的基线

| 项目 | 目标机 / donor |
| --- | --- |
| 目标机 | OnePlus Turbo 6，型号 `PLU110` |
| 目标系统 | `PLU110_16.0.2.408` / ColorOS 16 / Android 16 |
| SoC | Qualcomm SM8735（第四代骁龙 8s） |
| 首选 donor | Redmi Turbo 4 Pro / POCO F7，代号 `onyx` |
| donor 系统 | HyperOS 3 / Android 16 完整包 |

同 SoC 只代表 bring-up 成功率更高，不代表分区可以直接互刷。屏幕、触控、相机、指纹、充电、音频、传感器、TEE 和 SELinux 策略仍需要逐项适配。

## 安全边界

- 绝不刷入 donor 的 `boot`、`init_boot`、`vendor_boot`、`dtbo`、`modem`、`bluetooth`、`dsp`、`abl`、`xbl`、`tz` 或其他固件分区。
- 未验证 anti-rollback、AVB 和动态分区布局前，不生成刷机脚本。
- 不要重新锁定 Bootloader；移植 ROM 下重新上锁可能导致无法启动或变砖。
- 不把原厂 ROM、私有 blob、密钥或带签名参数的下载链接提交到公开仓库。
- 首次测试必须保留当前版本的完整救砖包，并确认可以进入 bootloader/fastbootd。

## 第一步：采集真机信息

在已安装 Android platform-tools 的电脑上连接手机并开启 USB 调试：

```bash
bash scripts/collect-device-info.sh
python3 tools/validate_device_report.py device-info-*.txt
```

若要采集 bootloader 信息，请先自行进入 bootloader，再运行只读脚本：

```bash
bash scripts/collect-fastboot-info.sh
```

这两个脚本只读取移植所需信息，不解锁、不擦除、不刷写任何分区，也不采集 IMEI 或设备序列号。

## 第二步：校验 ROM 输入

需要两个完整包：

1. 与手机当前版本完全对应的 `PLU110_16.0.2.408` ColorOS 16 / Android 16 全量包。
2. `onyx` 的 HyperOS 3 / Android 16 完整 Recovery OTA 或 Fastboot 包。

本地校验：

```bash
python3 tools/rom_preflight.py \
  --base /path/to/PLU110-full-ota.zip \
  --donor /path/to/onyx-hyperos3.zip \
  --output reports/rom-preflight.json
```

GitHub Actions 校验：在仓库的 Actions secrets 中设置 `BASE_ROM_URL`、`DONOR_ROM_URL`，并建议同时设置 `BASE_ROM_SHA256`、`DONOR_ROM_SHA256`，然后手动运行 **ROM preflight**。不要把含访问令牌的 URL 填进普通 workflow input。

## Bring-up 路线

1. 锁定原厂版本、分区表、super 容量、VINTF 和 AVB 状态。
2. 解包 base 与 donor，生成逐分区清单和差异报告。
3. 保留目标机硬件栈，适配 donor 的 `system`、`system_ext`、`product` 与 `mi_ext`。
4. 修复 VINTF、init、属性、overlay、权限和 SELinux 冲突。
5. 生成仅供测试的 fastbootd 包，并加入型号、版本和分区容量硬校验。
6. 按“开机 → 基带 → Wi-Fi/蓝牙 → 触控/显示 → 音频 → 相机 → 指纹 → 充电/NFC”的顺序验证。

更完整的阶段说明见 [`docs/BRINGUP.md`](docs/BRINGUP.md)。

## 仓库内容

- `scripts/collect-device-info.sh`：采集 Android 侧只读报告。
- `scripts/collect-fastboot-info.sh`：采集 bootloader 侧只读报告。
- `tools/validate_device_report.py`：验证型号、Android 版本、SoC 和解锁状态。
- `tools/rom_preflight.py`：验证 base/donor 身份、Android 版本和 OTA 结构。
- `.github/workflows/ci.yml`：脚本语法和单元测试。
- `.github/workflows/rom-preflight.yml`：使用仓库 Secrets 下载并校验 ROM。

## 许可与固件

仓库中的原创脚本使用 MIT License。OnePlus、OPPO、Xiaomi、Redmi、POCO、ColorOS、HyperOS 及相关固件属于各自权利人；本仓库不授予重新分发其固件的权利。
