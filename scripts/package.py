"""Allowlisted source export: no local state, credentials, pilot records or history."""
import argparse
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from saha_agent import __version__

FILES = {
    'README.md', 'LICENSE', 'CHANGELOG.md', 'CONTRIBUTING.md', 'SECURITY.md',
    'VERIFICATION.md', 'pyproject.toml', '.gitignore', 'config.example.json',
    'requirements-slack.txt', 'plugin.json', 'mcp.json', '.mcp.json',
    '.claude-plugin/plugin.json', 'slack-app-manifest.json',
    'docs/INSTALLATION.md', 'docs/CONFIGURATION.md', 'docs/SLACK-SETUP.md',
    'docs/ARCHITECTURE.md', 'docs/TROUBLESHOOTING.md', 'docs/MIGRATION.md',
}
TREES = {'saha_agent': {'.py', '.json', '.html', '.css', '.js'},
         'tests': {'.py'}, 'scripts': {'.py'}, 'skills': {'.md'},
         'commands': {'.md'}, 'hooks': {'.json'}, '.github/workflows': {'.yml', '.yaml'}}


def source_files(root=ROOT):
    found = set()
    for relative in FILES:
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError('Missing or symlinked release file: ' + relative)
        found.add(path)
    for tree, suffixes in TREES.items():
        for path in (root/tree).rglob('*'):
            relative=path.relative_to(root)
            if (not path.is_file() or path.is_symlink() or path.suffix not in suffixes
                    or '__pycache__' in relative.parts):
                continue
            if any(parent.is_symlink() for parent in path.parents if parent != root and root in parent.parents):
                raise ValueError('Symlinked release directory')
            found.add(path)
    return sorted(found)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, help='Export to a new directory, without Git history')
    parser.add_argument('--output', type=Path, help='Zip destination (default: sibling versioned zip)')
    args = parser.parse_args()
    files=source_files()
    if args.directory:
        target=args.directory.resolve()
        if target.exists():raise ValueError('Export destination must not exist')
        target.mkdir(parents=True)
        for path in files:
            dest=target/path.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(path,dest)
    else:
        target=args.output or ROOT.parent/f'saha-agent-{__version__}.zip'
        with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
            for path in files:archive.write(path,'saha-agent/'+path.relative_to(ROOT).as_posix())
        with zipfile.ZipFile(target) as archive:
            if archive.testzip() is not None:raise ValueError('Archive verification failed')
    print(f'{target} ({len(files)} allowlisted source files; no runtime state or Git history)')


if __name__=='__main__':main()
