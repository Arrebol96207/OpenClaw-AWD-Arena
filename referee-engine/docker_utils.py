"""Docker 容器操作工具函数"""

import asyncio
from typing import List, Optional, Tuple


async def docker_exec(
    container_name: str,
    command: List[str],
    *,
    timeout: int = 30,
    user: Optional[str] = None,
    stdin_text: Optional[str] = None,
    raise_on_error: bool = True,
) -> Tuple[int, str, str]:
    """
    在 Docker 容器中执行命令

    Args:
        container_name: 容器名称
        command: 要执行的命令列表
        timeout: 超时时间（秒）
        user: 执行命令的用户
        stdin_text: 标准输入文本
        raise_on_error: 是否在非零返回码时抛出异常

    Returns:
        (returncode, stdout, stderr) 元组

    Raises:
        RuntimeError: 当 raise_on_error=True 且命令执行失败时
        asyncio.TimeoutError: 当命令超时时
    """
    docker_command = ["docker", "exec"]
    if stdin_text is not None:
        docker_command.append("-i")
    if user:
        docker_command.extend(["-u", user])
    docker_command.append(container_name)
    docker_command.extend(command)

    proc = await asyncio.create_subprocess_exec(
        *docker_command,
        stdin=asyncio.subprocess.PIPE if stdin_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(
        proc.communicate(stdin_text.encode("utf-8") if stdin_text is not None else None),
        timeout=timeout,
    )

    stdout_text = stdout.decode("utf-8", errors="replace")
    stderr_text = stderr.decode("utf-8", errors="replace").strip()
    returncode = int(proc.returncode or 0)

    if raise_on_error and returncode != 0:
        details = stderr_text or f"rc={returncode}"
        stdout_detail = stdout_text.strip()
        if stdout_detail:
            details = f"{details}; stdout: {stdout_detail}"
        raise RuntimeError(
            f"docker exec failed for {container_name}: {details}"
        )

    return returncode, stdout_text, stderr_text


async def docker_exec_simple(
    container_name: str,
    command: List[str],
    *,
    timeout: int = 10,
) -> Tuple[int, str, str]:
    """
    简化版的 docker exec，不支持 stdin 和 user 参数

    Args:
        container_name: 容器名称
        command: 要执行的命令列表
        timeout: 超时时间（秒）

    Returns:
        (returncode, stdout, stderr) 元组
    """
    return await docker_exec(
        container_name,
        command,
        timeout=timeout,
        raise_on_error=False,
    )
