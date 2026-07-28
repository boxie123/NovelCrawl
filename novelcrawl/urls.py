"""URL 规范化工具。

同一资源可能带有大小写不同的域名或 ``#fragment``。入库前规范化可减少
重复来源和重复章节；查询参数会保留，因为某些网站用它区分章节。
"""

from urllib.parse import urlparse, urlunparse


def normalize_url(url: str) -> str:
    """生成适合用作数据库身份的 URL。

    Args:
        url: 原始绝对 URL，允许首尾空白。

    Returns:
        协议和域名转为小写、空路径补为 ``/``、移除 fragment 后的 URL。
    """
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    return urlunparse(
        (
            scheme,
            netloc,
            path,
            parsed.params,
            parsed.query,
            # fragment 只定位页面内部锚点，不属于服务器资源身份。
            "",
        )
    )
