"""使用官方Android SDK和本机JDK构建无第三方运行依赖的本地调试APK。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile


PROJECT = Path(__file__).resolve().parent
WORKSPACE = PROJECT.parent


def run(arguments: list[str | Path]) -> str:
    """执行明确的工具参数列表，失败时保留诊断且不通过shell拼接命令。"""
    result = subprocess.run([str(item) for item in arguments], cwd=PROJECT, check=False,
                            text=True, encoding="utf-8", errors="replace", capture_output=True)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip())
    result.check_returncode()
    return result.stdout + result.stderr


def locate(root: Path, name: str) -> Path:
    """在用户指定的SDK工具目录定位唯一需要的工具，不修改系统PATH。"""
    matches = list(root.rglob(name))
    if not matches:
        raise FileNotFoundError(f"未找到{name}，请先安装Android SDK或下载讲义中的官方工具")
    return matches[0]


def main() -> None:
    """编译、DEX转换、打包、开发签名并验证APK，同时输出可核验的构建记录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, default=WORKSPACE / "local-data/android-sdk-tooling")
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    for command in ("java", "javac", "keytool"):
        if not shutil.which(command):
            raise FileNotFoundError(f"需要JDK工具：{command}")
    android_jar = locate(sdk, "android.jar")
    aapt2 = locate(sdk, "aapt2.exe")
    d8 = locate(sdk, "d8.jar")
    apksigner = locate(sdk, "apksigner.jar")
    zipalign = locate(sdk, "zipalign.exe")
    dist = PROJECT / "dist"
    dist.mkdir(exist_ok=True)
    keystore = sdk / "student-assistant-development.p12"
    if not keystore.exists():
        # 这是本地开发签名，不是发布证书；android是公开约定的调试口令。
        run(["keytool", "-genkeypair", "-keystore", keystore, "-storetype", "PKCS12",
             "-alias", "student-assistant-debug", "-storepass", "android", "-keypass", "android",
             "-dname", "CN=Student Assistant Local Development", "-keyalg", "RSA", "-keysize", "2048",
             "-validity", "3650", "-noprompt"])
    with tempfile.TemporaryDirectory(prefix="android-bridge-build-", dir=sdk) as temporary:
        build = Path(temporary)
        classes = build / "classes"
        dex = build / "dex"
        classes.mkdir()
        dex.mkdir()
        sources = sorted((PROJECT / "src").rglob("*.java"))
        run(["javac", "-J-Duser.language=en", "-encoding", "UTF-8", "--release", "8", "-classpath",
             android_jar, "-d", classes, *sources])
        test_classes = build / "test-classes"
        test_classes.mkdir()
        classpath = str(classes) + ";" + str(android_jar)
        run(["javac", "-J-Duser.language=en", "-encoding", "UTF-8", "--release", "8", "-classpath", classpath,
             "-d", test_classes, PROJECT / "tests/ServerValidationTest.java"])
        validation = run(["java", "-classpath", str(test_classes) + ";" + classpath, "ServerValidationTest"])
        classes_jar = build / "classes.jar"
        with zipfile.ZipFile(classes_jar, "w", zipfile.ZIP_DEFLATED) as archive:
            for source in classes.rglob("*.class"):
                archive.write(source, source.relative_to(classes).as_posix())
        # D8转换包含lambda所需的兼容字节码，最低Android版本为8.0/API26。
        run(["java", "-cp", d8, "com.android.tools.r8.D8", "--lib", android_jar,
             "--min-api", "26", "--output", dex, classes_jar])
        unsigned = build / "unsigned.apk"
        aligned = build / "aligned.apk"
        run([aapt2, "link", "-o", unsigned, "-I", android_jar, "--manifest", PROJECT / "AndroidManifest.xml"])
        with zipfile.ZipFile(unsigned, "a", zipfile.ZIP_DEFLATED) as archive:
            for source in dex.glob("classes*.dex"):
                archive.write(source, source.name)
        run([zipalign, "-f", "4", unsigned, aligned])
        apk = dist / "student-assistant-bridge.apk"
        run(["java", "-jar", apksigner, "sign", "--ks", keystore, "--ks-key-alias", "student-assistant-debug",
             "--ks-pass", "pass:android", "--key-pass", "pass:android", "--out", apk, aligned])
        verification = run(["java", "-jar", apksigner, "verify", "--verbose", "--print-certs", apk])
        run([zipalign, "-c", "4", apk])
        record = {"artifact": apk.name, "bytes": apk.stat().st_size,
                  "sha256": hashlib.sha256(apk.read_bytes()).hexdigest(),
                  "package": "com.studentassistant.bridge", "version": "1.0", "min_sdk": 26,
                  "target_sdk": 35, "source_files": len(sources), "signature_verified": True,
                  "desktop_url_validation": validation.strip(),
                  "source_sha256": {source.relative_to(PROJECT).as_posix(): hashlib.sha256(source.read_bytes()).hexdigest()
                                    for source in [PROJECT / "AndroidManifest.xml", *sources]},
                  "device_install_tested": False, "real_notifications_tested": False,
                  "verification": verification.strip()}
        (dist / "build_result.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in record.items() if key != "verification"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
