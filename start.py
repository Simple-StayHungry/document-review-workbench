"""Cross-platform zero-install launcher for the local verification workbench."""
from __future__ import annotations
import hashlib,json,os,sys,urllib.request,webbrowser,shutil,xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path(__file__).resolve().parent
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from workbench.version import BUILD
PROJECT_ADDIN_NAMES={'ka-transaction-review'}
PROJECT_ADDIN_PREFIXES=('ka-transaction-review',)

def supported():
    if sys.version_info<(3,10):raise RuntimeError('需要 Python 3.10 或更高版本。建议安装 Python 3.12、3.13 或 3.14 后再次启动。')

def _wps_addon_bases():
    """Locate every plausible WPS JS-addin registry used by this project.

    Older experimental builds were installed under both `jsaddons` and sibling
    add-on folders, and some WPS releases keep the registry in the global container.
    Search only WPS/Kingsoft roots, then remove only our known add-in name/prefix.
    """
    home=Path.home();candidates=[]
    if sys.platform=='darwin':
        roots=[home/'Library/Containers',home/'Library/Application Support/Kingsoft']
        for base in roots:
            if not base.exists():continue
            if base.name=='Containers':
                containers=[p for p in base.glob('com.kingsoft.wpsoffice.mac*') if p.is_dir()]
                for c in containers:
                    data=c/'Data'
                    for q in (data/'.kingsoft/wps/jsaddons',data/'.kingsoft/wps/addons',data/'Library/Application Support/Kingsoft/WPS/jsaddons',data/'Library/Application Support/Kingsoft/WPS/addons'):
                        if q.exists():candidates.append(q)
                    king=data/'.kingsoft'
                    if king.exists():
                        for pub in king.glob('wps/**/publish.xml'):candidates.append(pub.parent)
            else:
                for pub in base.glob('**/publish.xml'):candidates.append(pub.parent)
    elif os.name=='nt':
        for env in ('APPDATA','LOCALAPPDATA'):
            value=os.environ.get(env)
            if not value:continue
            base=Path(value)/'kingsoft'
            for q in (base/'wps/jsaddons',base/'wps/addons',base/'office6/jsaddons',base/'office6/addons'):
                if q.exists():candidates.append(q)
            if base.exists():
                for pub in base.glob('**/publish.xml'):candidates.append(pub.parent)
    elif sys.platform.startswith('linux'):
        for q in (home/'.local/share/Kingsoft/wps/jsaddons',home/'.local/share/Kingsoft/wps/addons'):
            if q.exists():candidates.append(q)
    out=[];seen=set()
    for q in candidates:
        try:key=str(q.resolve())
        except Exception:key=str(q)
        if key not in seen:seen.add(key);out.append(q)
    return out

def _remove_project_addins(base:Path):
    """Remove every historical WPS add-in created by this project only.

    Unrelated WPS/third-party add-ins are deliberately preserved.  Early builds used
    ka-transaction-review plus timestamped/backup publish files; clear all of those
    so no old listener can affect native review navigation.
    """
    changed=False
    if not base.exists():return False,True
    for child in list(base.iterdir()):
        if child.is_dir() and (child.name in PROJECT_ADDIN_NAMES or any(child.name.startswith(prefix) for prefix in PROJECT_ADDIN_PREFIXES)):
            shutil.rmtree(child,ignore_errors=False);changed=True
    publish=base/'publish.xml'
    if publish.is_file():
        raw=publish.read_bytes()
        try:root=ET.fromstring(raw)
        except ET.ParseError:
            return changed,False
        removed=False
        for child in list(root):
            name=(child.get('name') or '').strip()
            url=(child.get('url') or '').strip()
            if name in PROJECT_ADDIN_NAMES or any(name.startswith(prefix) or prefix in url for prefix in PROJECT_ADDIN_PREFIXES):
                root.remove(child);removed=True
        if removed:
            temp=publish.with_suffix('.xml.tmp')
            temp.write_bytes(ET.tostring(root,encoding='utf-8',xml_declaration=True))
            temp.replace(publish);changed=True
    # Backups were created only by our experimental installers and are not active
    # plug-ins; remove them as part of the one-time cleanup to avoid future restore.
    for pat in ('publish.xml.ka-backup-*','publish.xml.ka-unparsed-backup'):
        for backup in base.glob(pat):
            try:backup.unlink();changed=True
            except FileNotFoundError:pass
    return changed,True

def cleanup_legacy_wps_addin():
    changed=False;warning=False
    for base in _wps_addon_bases():
        try:
            c,ok=_remove_project_addins(base);changed=changed or c;warning=warning or not ok
        except Exception:
            warning=True
    return changed,warning

def main():
    supported();os.chdir(ROOT)
    # Starting a document workbench must not modify the user's Office installation.
    # Legacy cleanup remains an explicitly invoked maintenance command only.
    # This build deliberately installs no WPS add-in. Exported DOCX files use
    # ordinary WPS/Word tracked changes only, so accepting/rejecting a revision
    # never causes this program to reload or reposition the active document.
    for port in range(8766,8796):
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f'http://127.0.0.1:{port}/api/health',timeout=.18) as r:
                health=json.load(r)
                if health.get('app')=='local-verification-workbench' and health.get('build')==BUILD and health.get('instance')==hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]:
                    print('核查工作台已经在运行，正在打开浏览器。');webbrowser.open(f'http://127.0.0.1:{port}');return
        except Exception:pass
    from server import serve
    serve(open_browser=True)

if __name__=='__main__':
    try:main()
    except Exception as ex:
        print('\n启动未完成：'+str(ex),file=sys.stderr)
        print('程序不会联网安装依赖。若仍无法启动，请保留本窗口截图。',file=sys.stderr)
        if sys.stdin.isatty():input('按回车关闭。')
        sys.exit(1)
