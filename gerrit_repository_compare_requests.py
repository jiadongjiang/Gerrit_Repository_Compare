import json
import os
from typing import List, Dict, Any

import requests
# 可选：若必须 verify=False，则关闭相关警告
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def strip_gerrit_json(text: str) -> str:
    # Gerrit REST 会返回前缀 ")]}'\n"
    prefix = ")]}'"
    if text.startswith(prefix):
        # 常见是前缀加换行，稳妥切一行
        return text.split("\n", 1)[1] if "\n" in text else ""
    return text


def Repository_Compare(base_url: str, auth, change_id: str, timeout: float = 15.0) -> List[str]:
    session = requests.Session()
    session.auth = auth
    # 如需开启证书验证，将 verify=True
    session.verify = False

    # 1) 获取 change 信息，拿当前 revision
    change_url = f"{base_url}/a/changes/{change_id}?o=CURRENT_REVISION"
    resp = session.get(change_url, timeout=timeout)
    resp.raise_for_status()
    try:
        change_data = json.loads(strip_gerrit_json(resp.text))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Decode change response failed: {e}\nRaw: {resp.text[:200]}")

    revision_id = change_data.get("current_revision")
    if not revision_id:
        raise RuntimeError("No current_revision found. Check change_id or permissions.")

    # 2) 列出文件
    file_url = f"{base_url}/a/changes/{change_id}/revisions/{revision_id}/files"
    file_resp = session.get(file_url, timeout=timeout)
    file_resp.raise_for_status()
    try:
        file_data: Dict[str, Any] = json.loads(strip_gerrit_json(file_resp.text))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Decode files response failed: {e}\nRaw: {file_resp.text[:200]}")

    # Gerrit 文件列表包含特殊键，比如 "/COMMIT_MSG" "/MERGE_LIST"
    # 你原来是 list(file_data.keys())[1:]，这里更显式过滤
    special_keys = {"/COMMIT_MSG", "/MERGE_LIST"}
    file_list = [k for k in file_data.keys() if k not in special_keys]

    def is_ignored_file(path: str) -> bool:
        # 可自行扩展忽略列表
        ext_list = {".so", ".a", ".md", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".tar", ".gz", ".bz2"}
        _, ext = os.path.splitext(path)
        return ext.lower() in ext_list

    def repository_compare(diff_json: Dict[str, Any]) -> str:
        diff_info = diff_json.get("content", [])

        def format_compare(lines: List[str], prefix: str = "") -> str:
            return "".join(f"{prefix}{line}\n" for line in lines)

        output = ""
        for i, block in enumerate(diff_info):
            if "ab" in block:
                # 保留前后文，防止过长
                if i == 0:
                    output += format_compare(block["ab"][-4:])
                elif i == len(diff_info) - 1:
                    output += format_compare(block["ab"][0:4])
                else:
                    output += format_compare(block["ab"][0:4])
                    output += format_compare(block["ab"][-4:])
            else:
                if "a" in block and "b" in block:
                    output += format_compare(block["a"], "-")
                    output += format_compare(block["b"], "+")
                elif "a" in block:
                    output += format_compare(block["a"], "-")
                elif "b" in block:
                    output += format_compare(block["b"], "+")
        return output + "\n\n\n"

    def make_message(file_path: str) -> str:
        if is_ignored_file(file_path):
            return ""

        from urllib.parse import quote
        encoded_path = quote(file_path, safe="")
        path_url = f"{file_url}/{encoded_path}/diff"

        path_resp = session.get(path_url, timeout=timeout)
        path_resp.raise_for_status()
        try:
            return_data = json.loads(strip_gerrit_json(path_resp.text))
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Decode diff for {file_path} failed: {e}\nRaw: {path_resp.text[:200]}")

        message = f"======== file: {file_path}\n\n" + repository_compare(return_data)
        return message

    results: List[str] = []
    for file_path in file_list:
        try:
            msg = make_message(file_path)
            if msg:
                results.append(msg)
        except requests.HTTPError as e:
            results.append(f"======== file: {file_path}\n\n[HTTP error] {e}\n\n")
        except Exception as e:
            results.append(f"======== file: {file_path}\n\n[Error] {e}\n\n")

    return results


if __name__ == "__main__":
    # Gerrit 服务器地址 账号密码
    base_url = os.getenv("GERRIT_URL") or "gerrit_url"
    user = os.getenv("GERRIT_USER") or "your_user"
    password = os.getenv("GERRIT_PASS") or "your_name"
    auth = (user, password)

    # change_id 可用数字，或 project~branch~Change-Id
    change_id = "change_id"

    results = Repository_Compare(base_url, auth, change_id)
    for block in results:
        print(block)
