"""
GeoLite2 Helper Class
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import time

import httpx
import maxminddb

BASE_URL = "https://download.maxmind.com/app/geoip_download"
EDITIONS = {
    "City": "GeoLite2-City",
    "Country": "GeoLite2-Country",
    "ASN": "GeoLite2-ASN",
}


class GeoIPHelper:
    def __init__(
        self,
        dest_dir="./geoip",
        license_key=None,
        editions=None,
        max_age_days=8,
        timeout=60.0,
    ):
        self.dest_dir = dest_dir
        self.license_key = license_key or os.getenv("MAXMIND_LICENSE_KEY")
        self.editions = editions or ["City", "ASN"]
        self.max_age_days = max_age_days
        self.timeout = timeout
        self._readers = {}

    @staticmethod
    def _safe_extract(tar: tarfile.TarFile, path: str):
        base = Path(path).resolve()
        for m in tar.getmembers():
            target = (base / m.name).resolve()
            if not str(target).startswith(str(base)):
                raise RuntimeError("Unsafe path in tar file")
        tar.extractall(path=path, filter="data")

    def _download_and_extract(self, edition_id: str) -> str:
        """
        下载并解压 mmdb 文件到 dest_dir，仅保留 .mmdb
        - 跟随 302 重定向
        - 流式下载到临时文件
        - 临时目录退出后自动清理
        """
        if not self.license_key:
            raise ValueError("缺少 MaxMind License Key，请传入或设置环境变量 MAXMIND_LICENSE_KEY")

        url = f"{BASE_URL}?edition_id={edition_id}&license_key={self.license_key}&suffix=tar.gz"

        with httpx.Client(follow_redirects=True, timeout=self.timeout) as client:
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with tempfile.TemporaryDirectory() as tmpd:
                    tgz_path = os.path.join(tmpd, "db.tgz")
                    # 流式写入
                    with open(tgz_path, "wb") as f:
                        for chunk in resp.iter_bytes():
                            if chunk:
                                f.write(chunk)

                    # 解压并只移动 .mmdb
                    with tarfile.open(tgz_path, "r:gz") as tar:
                        # 先安全检查与解压
                        self._safe_extract(tar, tmpd)

                    # 递归找 .mmdb
                    mmdb_path = None
                    for root, _, files in os.walk(tmpd):
                        for fn in files:
                            if fn.endswith(".mmdb"):
                                mmdb_path = os.path.join(root, fn)
                                break
                        if mmdb_path:
                            break

                    if not mmdb_path:
                        raise RuntimeError("未在压缩包中找到 .mmdb 文件")

                    os.makedirs(self.dest_dir, exist_ok=True)
                    dst = os.path.join(self.dest_dir, os.path.basename(mmdb_path))
                    shutil.move(mmdb_path, dst)
                    return dst

    def _latest_file(self, edition_id: str):
        if not os.path.isdir(self.dest_dir):
            return None
        files = [
            os.path.join(self.dest_dir, f)
            for f in os.listdir(self.dest_dir)
            if f.startswith(edition_id) and f.endswith(".mmdb")
        ]
        return max(files, key=os.path.getmtime) if files else None

    def update(self, force=False):
        from app.log import logger

        for ed in self.editions:
            eid = EDITIONS[ed]
            path = self._latest_file(eid)
            need = force or not path

            if path:
                age_days = (time.time() - os.path.getmtime(path)) / 86400
                if age_days >= self.max_age_days:
                    need = True
                    logger.info(
                        f"[GeoIP] {eid} database is {age_days:.1f} days old "
                        f"(max: {self.max_age_days}), will download new version"
                    )
                else:
                    logger.info(
                        f"[GeoIP] {eid} database is {age_days:.1f} days old, still fresh (max: {self.max_age_days})"
                    )
            else:
                logger.info(f"[GeoIP] {eid} database not found, will download")

            if need:
                logger.info(f"[GeoIP] Downloading {eid} database...")
                path = self._download_and_extract(eid)
                logger.info(f"[GeoIP] {eid} database downloaded successfully")
            else:
                logger.info(f"[GeoIP] Using existing {eid} database")

            old = self._readers.get(ed)
            if old:
                try:
                    old.close()
                except Exception:
                    pass
            if path is not None:
                self._readers[ed] = maxminddb.open_database(path)

    def lookup(self, ip: str):
        res = {"ip": ip}
        # City
        city_r = self._readers.get("City")
        if city_r:
            data = city_r.get(ip)
            if data:
                country = data.get("country") or {}
                res["country_iso"] = country.get("iso_code") or ""
                res["country_name"] = (country.get("names") or {}).get("en", "")
                city = data.get("city") or {}
                res["city_name"] = (city.get("names") or {}).get("en", "")
                loc = data.get("location") or {}
                res["latitude"] = str(loc.get("latitude") or "")
                res["longitude"] = str(loc.get("longitude") or "")
                res["time_zone"] = str(loc.get("time_zone") or "")
                postal = data.get("postal") or {}
                if "code" in postal:
                    res["postal_code"] = postal["code"]
        # ASN
        asn_r = self._readers.get("ASN")
        if asn_r:
            data = asn_r.get(ip)
            if data:
                res["asn"] = data.get("autonomous_system_number")
                res["organization"] = data.get("autonomous_system_organization")
        return res

    def close(self):
        for r in self._readers.values():
            try:
                r.close()
            except Exception:
                pass
        self._readers = {}


if __name__ == "__main__":
    # 示例用法
    geo = GeoIPHelper(dest_dir="./geoip", license_key="")
    geo.update()
    print(geo.lookup("8.8.8.8"))
    geo.close()
