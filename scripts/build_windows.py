"""Public local Windows builder. No downloads, uploads, or security-setting changes."""
import argparse, hashlib, importlib.metadata, json, os, platform, shutil, subprocess, sys
from pathlib import Path
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=Path("dist"))
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    if sys.platform!="win32" or platform.machine().lower() not in ("amd64","x86_64") or sys.maxsize<=2**32:
        parser.error("Windows x64 Python required")
    importlib.metadata.version("pyinstaller")
    subprocess.run([sys.executable,str(root/"run_build_tests.py")],cwd=root,check=True)
    output=args.output.resolve()
    if not output.is_relative_to(root):
        parser.error("Output must stay inside this checkout")
    if (output/"AutoFusionCommunity").exists():
        parser.error("Output already exists; choose another --output")
    work=root/"build"
    if not work.resolve().is_relative_to(root):
        parser.error("Build directory must stay inside this checkout")
    work.mkdir(exist_ok=True)
    temporary=work/"tmp"
    temporary.mkdir(exist_ok=True)
    for key in ("TEMP","TMP"):
        os.environ[key]=str(temporary)
    os.environ["PYINSTALLER_CONFIG_DIR"]=str(work/"pyinstaller-cache")
    cmd=[sys.executable,"-m","PyInstaller","--noconfirm","--onedir","--windowed","--noupx",
         "--name","AutoFusionCommunity","--distpath",str(output),"--workpath",str(work/"pyinstaller"),
         "--specpath",str(work),"--add-data",str(root/"modules_catalog.json")+";.",
         "--add-data",str(root/"update_source.json")+";.",str(root/"app.py")]
    with (work/"build.log").open("w",encoding="utf-8") as log:
        proc=subprocess.Popen(cmd,cwd=root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors="replace")
        for line in proc.stdout:
            print(line,end="");log.write(line)
        if proc.wait(): raise SystemExit("Build failed; see build/build.log")
    result=output/"AutoFusionCommunity"
    shutil.copytree(root/"licenses",result/"licenses")
    for name in ("README.md","LICENSE_POLICY.md","THIRD_PARTY_NOTICES.md") + tuple(n for n in ("LICENSE","LICENSE_CANDIDATE.md") if (root/n).exists()):
        shutil.copy2(root/name,result/name)
    manifest={}
    for p in sorted(result.rglob("*")):
        if p.is_file(): manifest[p.relative_to(result).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    (output/"SHA256_MANIFEST.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    print("Private build only. License and security review required before public distribution.")
if __name__=="__main__": main()
