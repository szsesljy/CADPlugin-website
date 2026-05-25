import asyncio
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2)

_CLAMDSCAN_AVAILABLE = None


async def scan_file(filepath: Path) -> dict:
    """用 ClamAV 扫描文件（优先 clamdscan，毫秒级；回退 clamscan）。

    返回示例:
      {"clean": True}
      {"clean": False, "virus": "Win.Trojan.Agent-xxx FOUND"}
      {"clean": True, "skipped": True}  — ClamAV 未安装时跳过
    """
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(_executor, _scan, str(filepath))
        return result
    except Exception as e:
        logger.exception("ClamAV 扫描异常")
        return {"clean": False, "error": str(e)}


def _scan(filepath: str) -> dict:
    global _CLAMDSCAN_AVAILABLE

    # 优先使用 clamdscan（快，需要运行 clamd 服务）
    if _CLAMDSCAN_AVAILABLE is None or _CLAMDSCAN_AVAILABLE:
        try:
            proc = subprocess.run(
                ["clamdscan", "--stdout", "--no-summary", filepath],
                capture_output=True,
                text=True,
                timeout=60,
            )
            _CLAMDSCAN_AVAILABLE = True
            return _parse_result(proc, "clamdscan")
        except FileNotFoundError:
            _CLAMDSCAN_AVAILABLE = False
            logger.info("clamdscan 不可用，回退到 clamscan（较慢）")
        except subprocess.TimeoutExpired:
            logger.error("clamdscan 超时 (60s): %s", filepath)
            return {"clean": False, "error": "扫描超时"}

    # 回退到 clamscan（每次加载病毒库，较慢）
    try:
        proc = subprocess.run(
            ["clamscan", "--stdout", filepath],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return _parse_result(proc, "clamscan")
    except FileNotFoundError:
        logger.warning("ClamAV 未安装，跳过扫描 (yum install clamav clamav-update && freshclam)")
        return {"clean": True, "skipped": True}
    except subprocess.TimeoutExpired:
        logger.error("ClamAV 扫描超时 (120s): %s", filepath)
        return {"clean": False, "error": "扫描超时"}


def _parse_result(proc: subprocess.CompletedProcess, source: str) -> dict:
    output = proc.stdout + proc.stderr

    for line in output.splitlines():
        if "FOUND" in line:
            logger.warning("检测到病毒 [%s]: %s", source, line.strip())
            return {"clean": False, "virus": line.strip()}

    if proc.returncode == 0:
        return {"clean": True}

    logger.warning("ClamAV [%s] 异常退出 (code=%d)", source, proc.returncode)
    return {"clean": False, "error": f"{source} 退出码 {proc.returncode}"}
