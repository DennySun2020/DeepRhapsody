"""C/C++ toolchain detection, compilation, and server."""

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from debug_common import BaseDebugServer, find_repo_root
from debuggers.cpp_gdb import GdbDebugger
from debuggers.cpp_lldb import LldbDebugger
from debuggers.cpp_cdb import CdbDebugger


SOURCE_EXTENSIONS = {'.c', '.cpp', '.cc', '.cxx'}
CPP_EXTENSIONS = {'.cpp', '.cc', '.cxx'}


class ToolchainInfo:

    def __init__(self):
        self._vs_path = self._find_vs_install_path() if sys.platform == 'win32' else None
        self.platform_info = self._detect_platform()
        self.compilers = self._detect_compilers()
        self.debuggers = self._detect_debuggers()


    @staticmethod
    def _detect_platform() -> dict:
        os_name_map = {
            'win32': 'Windows', 'linux': 'Linux',
            'darwin': 'macOS', 'freebsd': 'FreeBSD',
        }
        return {
            'os': sys.platform,
            'os_name': os_name_map.get(sys.platform, sys.platform),
            'arch': platform.machine(),
        }


    @staticmethod
    def _find_vs_install_path() -> Optional[str]:
        pf86 = os.environ.get('ProgramFiles(x86)',
                              os.environ.get('ProgramW6432', r'C:\Program Files (x86)'))
        pf = os.environ.get('ProgramFiles', r'C:\Program Files')
        for vswhere in [
            os.path.join(pf86, 'Microsoft Visual Studio', 'Installer', 'vswhere.exe'),
            os.path.join(pf, 'Microsoft Visual Studio', 'Installer', 'vswhere.exe'),
        ]:
            if not os.path.isfile(vswhere):
                continue
            try:
                r = subprocess.run(
                    [vswhere, '-latest', '-property', 'installationPath'],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout.strip()
            except Exception:
                pass
        return None

    def _preferred_llvm_arch_dirs(self) -> list:
        arch = platform.machine().lower()
        if arch in ('amd64', 'x86_64', 'x64'):
            return ['x64', '']  # x64 first, then root/x86
        elif arch in ('arm64', 'aarch64'):
            return ['ARM64', 'x64', '']
        else:
            return ['', 'x64']

    def _find_vs_llvm_tool(self, tool_name: str) -> Optional[str]:
        if not self._vs_path:
            return None
        llvm_base = os.path.join(self._vs_path, 'VC', 'Tools', 'Llvm')
        if not os.path.isdir(llvm_base):
            return None
        for arch_dir in self._preferred_llvm_arch_dirs():
            if arch_dir:
                candidate = os.path.join(llvm_base, arch_dir, 'bin', tool_name)
            else:
                candidate = os.path.join(llvm_base, 'bin', tool_name)
            if os.path.isfile(candidate):
                return candidate
        return None

    @staticmethod
    def _validate_tool(path: str, timeout: float = 10.0,
                       version_flag: str = '--version') -> bool:
        """Verify a binary can start by running a version flag.

        Returns False if the binary crashes, hangs, or has missing DLLs.
        ``version_flag`` defaults to '--version' but can be overridden
        (e.g. CDB uses '-version').
        """
        try:
            r = subprocess.run(
                [path, version_flag],
                capture_output=True, text=True, timeout=timeout,
            )
            # Accept 0 (success) or 1 (some tools use 1 for --version)
            return r.returncode in (0, 1)
        except subprocess.TimeoutExpired:
            return False
        except OSError:
            return False


    def _detect_compilers(self) -> list:
        found = []
        # MSVC (Windows only)
        if sys.platform == 'win32':
            msvc = self._find_msvc()
            if msvc:
                found.append(msvc)
        # GCC
        for name in ('gcc', 'cc'):
            path = shutil.which(name)
            if path:
                found.append({
                    'name': 'gcc', 'path': path,
                    'version': self._tool_version(path),
                    'debug_format': 'dwarf',
                })
                break
        # G++
        path = shutil.which('g++')
        if path:
            found.append({
                'name': 'g++', 'path': path,
                'version': self._tool_version(path),
                'debug_format': 'dwarf',
            })
        # Clang
        path = shutil.which('clang')
        if not path and sys.platform == 'win32':
            path = self._find_vs_llvm_tool('clang.exe')
        if path:
            found.append({
                'name': 'clang', 'path': path,
                'version': self._tool_version(path),
                'debug_format': 'dwarf',
            })
        # Clang++
        path = shutil.which('clang++')
        if not path and sys.platform == 'win32':
            path = self._find_vs_llvm_tool('clang++.exe')
        if path:
            found.append({
                'name': 'clang++', 'path': path,
                'version': self._tool_version(path),
                'debug_format': 'dwarf',
            })
        return found


    def _detect_debuggers(self) -> list:
        found = []
        # GDB from PATH
        path = shutil.which('gdb')
        if path and self._validate_tool(path):
            found.append({
                'name': 'gdb', 'path': path,
                'version': self._tool_version(path),
                'debug_formats': ['dwarf', 'pdb'],
            })
        # LLDB from PATH
        path = shutil.which('lldb')
        if path and self._validate_tool(path):
            found.append({
                'name': 'lldb', 'path': path,
                'version': self._tool_version(path),
                'debug_formats': ['dwarf'],
            })
        # Windows: search VS installation for LLDB
        if sys.platform == 'win32' and not any(d['name'] == 'lldb' for d in found):
            path = self._find_vs_llvm_tool('lldb.exe')
            if path and self._validate_tool(path):
                found.append({
                    'name': 'lldb', 'path': path,
                    'version': self._tool_version(path),
                    'debug_formats': ['dwarf'],
                })
        # macOS: xcrun lldb
        if sys.platform == 'darwin' and not any(d['name'] == 'lldb' for d in found):
            try:
                r = subprocess.run(
                    ['xcrun', '--find', 'lldb'],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0 and r.stdout.strip():
                    lldb_path = r.stdout.strip()
                    if self._validate_tool(lldb_path):
                        found.append({
                            'name': 'lldb', 'path': lldb_path,
                            'version': '', 'debug_formats': ['dwarf'],
                        })
            except Exception:
                pass
        # CDB (Windows Console Debugger from Windows SDK)
        if sys.platform == 'win32' and not any(d['name'] == 'cdb' for d in found):
            cdb_path = self._find_cdb()
            if cdb_path:
                # Get version in one call (also serves as validation)
                cdb_ver = self._tool_version_cdb(cdb_path)
                if cdb_ver:
                    found.append({
                        'name': 'cdb', 'path': cdb_path,
                        'version': cdb_ver,
                        'debug_formats': ['pdb'],
                    })
        return found

    def _find_cdb(self) -> Optional[str]:
        # Check PATH first (classic name)
        path = shutil.which('cdb') or shutil.which('cdb.exe')
        if path:
            return path
        # WinDbg from Microsoft Store uses arch-suffixed names: cdbX64.exe, etc.
        arch = platform.machine().lower()
        if arch in ('amd64', 'x86_64', 'x64'):
            arch_suffixes = ['X64', 'X86']
        elif arch in ('arm64', 'aarch64'):
            arch_suffixes = ['ARM64', 'X64']
        else:
            arch_suffixes = ['X86', 'X64']
        for suffix in arch_suffixes:
            path = shutil.which(f'cdb{suffix}') or shutil.which(f'cdb{suffix}.exe')
            if path:
                return path
        # Search Windows Kits directories (SDK install)
        if arch in ('amd64', 'x86_64', 'x64'):
            arch_dirs = ['x64', 'x86']
        elif arch in ('arm64', 'aarch64'):
            arch_dirs = ['arm64', 'x64']
        else:
            arch_dirs = ['x86', 'x64']
        pf86 = os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')
        pf = os.environ.get('ProgramFiles', r'C:\Program Files')
        kit_roots = [
            os.path.join(pf86, 'Windows Kits', '10', 'Debuggers'),
            os.path.join(pf86, 'Windows Kits', '11', 'Debuggers'),
            os.path.join(pf, 'Windows Kits', '10', 'Debuggers'),
            os.path.join(pf, 'Windows Kits', '11', 'Debuggers'),
        ]
        for kit in kit_roots:
            for arch_dir in arch_dirs:
                candidate = os.path.join(kit, arch_dir, 'cdb.exe')
                if os.path.isfile(candidate):
                    return candidate
        # WinDbg from Microsoft Store (MSIX) — check WindowsApps for
        # arch-suffixed aliases (cdbX64.exe, cdbARM64.exe, etc.)
        local_app = os.environ.get('LOCALAPPDATA', '')
        if local_app:
            windbg_base = os.path.join(
                local_app, 'Microsoft', 'WindowsApps',
            )
            # Classic name first
            candidate = os.path.join(windbg_base, 'cdb.exe')
            if os.path.isfile(candidate):
                return candidate
            # Arch-suffixed names (new WinDbg from Store)
            for suffix in arch_suffixes:
                candidate = os.path.join(windbg_base, f'cdb{suffix}.exe')
                if os.path.isfile(candidate):
                    return candidate
        # Also check inside the MSIX package install directory
        try:
            r = subprocess.run(
                ['powershell', '-NoProfile', '-Command',
                 '(Get-AppxPackage -Name "Microsoft.WinDbg" '
                 '-ErrorAction SilentlyContinue).InstallLocation'],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                pkg_dir = r.stdout.strip()
                # Check for cdb.exe or arch-suffixed names inside the package
                for name in ['cdb.exe'] + [f'cdb{s}.exe' for s in arch_suffixes]:
                    candidate = os.path.join(pkg_dir, name)
                    if os.path.isfile(candidate):
                        return candidate
        except Exception:
            pass
        return None

    @staticmethod
    def _tool_version_cdb(path: str) -> str:
        try:
            r = subprocess.run(
                [path, '-version'],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip().split('\n')[0]
        except Exception:
            pass
        return ''


    def _find_msvc(self) -> Optional[dict]:
        # Already on PATH? (e.g. Developer Command Prompt)
        path = shutil.which('cl') or shutil.which('cl.exe')
        if path:
            return {
                'name': 'msvc', 'path': path,
                'version': '', 'debug_format': 'pdb',
            }
        # Search in Visual Studio installation (reuses cached _vs_path)
        if not self._vs_path:
            return None
        tools_root = os.path.join(self._vs_path, 'VC', 'Tools', 'MSVC')
        if not os.path.isdir(tools_root):
            return None
        # Prefer host x64 target x64, then other combos
        arch_dirs = [
            'Hostx64/x64', 'Hostx86/x64',
            'Hostx64/x86', 'Hostx86/x86',
        ]
        try:
            for ver in sorted(os.listdir(tools_root), reverse=True):
                for arch in arch_dirs:
                    cl = os.path.join(
                        tools_root, ver, 'bin',
                        arch.replace('/', os.sep), 'cl.exe',
                    )
                    if os.path.isfile(cl):
                        return {
                            'name': 'msvc', 'path': cl,
                            'version': ver, 'debug_format': 'pdb',
                        }
        except Exception:
            pass
        return None


    @staticmethod
    def _tool_version(path: str) -> str:
        try:
            r = subprocess.run(
                [path, '--version'],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip().split('\n')[0]
        except Exception:
            pass
        return ''


    def recommend(self, source_ext: str = '.c') -> dict:
        is_cpp = source_ext in CPP_EXTENSIONS
        cc_name = 'g++' if is_cpp else 'gcc'
        clangxx = 'clang++' if is_cpp else 'clang'

        def _find(items, name):
            return next((x for x in items if x['name'] == name), None)

        gdb  = _find(self.debuggers, 'gdb')
        lldb = _find(self.debuggers, 'lldb')
        cdb  = _find(self.debuggers, 'cdb')
        gcc  = _find(self.compilers, cc_name)
        clang = _find(self.compilers, clangxx)
        msvc = _find(self.compilers, 'msvc')

        # Best pairs in preference order
        pairs = [
            (msvc,  cdb,  'MSVC + CDB (native Windows debugger, reads PDB)'),
            (gcc,   gdb,  'GCC + GDB (best combination for DWARF debug info)'),
            (clang, lldb, 'Clang + LLDB (native LLVM toolchain)'),
            (clang, cdb,  'Clang + CDB (CDB can read PDB from clang-cl)'),
            (gcc,   lldb, 'GCC + LLDB'),
            (clang, gdb,  'Clang + GDB'),
            (msvc,  gdb,  'MSVC + GDB (note: GDB has limited PDB support)'),
        ]
        for cc, dbg, note in pairs:
            if cc and dbg:
                return {'compiler': cc, 'debugger': dbg, 'note': note}

        if self.compilers and not self.debuggers:
            return {
                'compiler': self.compilers[0], 'debugger': None,
                'note': 'Compiler found but no debugger. Install CDB (Windows), GDB, or LLDB.',
            }
        if self.debuggers and not self.compilers:
            return {
                'compiler': None, 'debugger': self.debuggers[0],
                'note': 'Debugger found but no compiler. Install GCC, Clang, or MSVC.',
            }
        if not self.compilers and not self.debuggers:
            return {
                'compiler': None, 'debugger': None,
                'note': self._install_instructions(),
            }
        return {
            'compiler': self.compilers[0],
            'debugger': self.debuggers[0],
            'note': '',
        }

    def _install_instructions(self) -> str:
        if sys.platform == 'win32':
            return (
                'No C/C++ build or debug tools found. Options:\n'
                '  1. Install Debugging Tools for Windows (includes CDB):\n'
                '     winget install Microsoft.WinDbg\n'
                '     or add "Debugging Tools" via Windows SDK installer\n'
                '  2. Install MSYS2 (https://www.msys2.org/) then:\n'
                '     pacman -S mingw-w64-x86_64-gcc mingw-w64-x86_64-gdb\n'
                '  3. Install Visual Studio Build Tools (includes cl.exe)\n'
                '  4. Install LLVM: winget install LLVM.LLVM'
            )
        if sys.platform == 'darwin':
            return (
                'No C/C++ build or debug tools found. Options:\n'
                '  1. Install Xcode Command Line Tools: xcode-select --install\n'
                '  2. Install GDB via Homebrew: brew install gdb'
            )
        return (
            'No C/C++ build or debug tools found. Options:\n'
            '  - Debian/Ubuntu: apt install build-essential gdb\n'
            '  - RHEL/CentOS:   yum install gcc gdb\n'
            '  - Arch:          pacman -S gcc gdb'
        )

    def to_dict(self) -> dict:
        return {
            'platform': self.platform_info,
            'compilers': self.compilers,
            'debuggers': self.debuggers,
            'recommendation': self.recommend(),
        }


# Recognized build system markers and how to invoke them.
# Each entry: (marker_file_or_glob, build_system_name, default_debug_command)
BUILD_SYSTEM_MARKERS = [
    # PowerShell build scripts (msquic-style, common in Microsoft repos)
    ('scripts/build.ps1',    'build.ps1',   'pwsh -NoProfile -File scripts/build.ps1 -Config Debug'),
    ('build.ps1',            'build.ps1',   'pwsh -NoProfile -File build.ps1 -Config Debug'),
    # CMake
    ('CMakeLists.txt',       'cmake',       'cmake -B build -DCMAKE_BUILD_TYPE=Debug && cmake --build build --config Debug'),
    ('CMakePresets.json',    'cmake',       'cmake --preset debug && cmake --build --preset debug'),
    # Make
    ('Makefile',             'make',        'make DEBUG=1'),
    ('makefile',             'make',        'make DEBUG=1'),
    ('GNUmakefile',          'make',        'make DEBUG=1'),
    # Meson
    ('meson.build',          'meson',       'meson setup builddir --buildtype debug && meson compile -C builddir'),
    # MSBuild / Visual Studio solution
    ('*.sln',                'msbuild',     'msbuild /p:Configuration=Debug'),
    # Ninja
    ('build.ninja',          'ninja',       'ninja'),
    # Cargo (Rust -- uses GDB/LLDB backends)
    ('Cargo.toml',           'cargo',       'cargo build'),
    # autotools
    ('configure',            'autotools',   './configure CFLAGS="-g -O0" && make'),
    ('configure.ac',         'autotools',   'autoreconf -i && ./configure CFLAGS="-g -O0" && make'),
    # Bazel
    ('BUILD',                'bazel',       'bazel build -c dbg //...'),
    ('BUILD.bazel',          'bazel',       'bazel build -c dbg //...'),
    ('WORKSPACE',            'bazel',       'bazel build -c dbg //...'),
]


def detect_build_system(repo_root: str) -> Optional[dict]:
    """Detect the build system used in a repository.

    Scans the repo root for known build files and returns a dict with:
        name:        Build system name (cmake, make, cargo, etc.)
        marker:      The file that was found
        default_cmd: Suggested command to build in debug mode
        build_dir:   Likely output directory (best guess)

    Returns None if no build system is detected.
    """
    import glob as _glob

    repo = os.path.abspath(repo_root)

    for marker, name, default_cmd in BUILD_SYSTEM_MARKERS:
        if '*' in marker:
            matches = _glob.glob(os.path.join(repo, marker))
            if matches:
                found_file = os.path.relpath(matches[0], repo)
                # For *.sln, customize the command
                if name == 'msbuild':
                    default_cmd = f'msbuild {found_file} /p:Configuration=Debug'
                return {
                    'name': name,
                    'marker': found_file,
                    'default_cmd': default_cmd,
                    'build_dir': _guess_build_dir(repo, name),
                }
        else:
            candidate = os.path.join(repo, marker)
            if os.path.isfile(candidate):
                return {
                    'name': name,
                    'marker': marker,
                    'default_cmd': default_cmd,
                    'build_dir': _guess_build_dir(repo, name),
                }

    return None


def _guess_build_dir(repo: str, build_system: str) -> str:
    guesses_map = {
        'cmake':    ['build', 'out', 'build/Debug', 'out/Debug'],
        'make':     ['.', 'build', 'out'],
        'meson':    ['builddir'],
        'msbuild':  ['Debug', 'x64/Debug', 'bin/Debug'],
        'ninja':    ['build', 'out'],
        'cargo':    ['target/debug'],
        'autotools': ['.', 'build'],
        'bazel':    ['bazel-bin'],
        'build.ps1': ['build', 'artifacts', 'bld', 'build/Debug',
                       'artifacts/bin', 'build/windows/x64_Debug'],
    }
    guesses = guesses_map.get(build_system, ['build', 'out', '.'])
    for d in guesses:
        full = os.path.join(repo, d)
        if os.path.isdir(full):
            return d
    return guesses[0]  # return first guess even if it doesn't exist yet


EXECUTABLE_EXTENSIONS = {'.exe', '.out', ''}
SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'venv',
             '.tox', '.mypy_cache', 'submodules', '.github'}


def find_binaries(
    search_dirs: List[str],
    name_hint: Optional[str] = None,
    test_only: bool = False,
) -> List[dict]:
    """Find executable binaries in build output directories.

    Args:
        search_dirs: Directories to search (absolute or relative).
        name_hint:   Substring to match in the binary name (case-insensitive).
        test_only:   If True, only return binaries with 'test' in the name.

    Returns:
        List of dicts: {path, name, size, is_test}
        Sorted by relevance (name_hint match first, then size descending).
    """
    results = []
    seen = set()
    test_patterns = re.compile(r'test|_test|tests|_tests|Test', re.I)

    for search_dir in search_dirs:
        search_dir = os.path.abspath(search_dir)
        if not os.path.isdir(search_dir):
            continue
        for root, dirs, files in os.walk(search_dir):
            # Skip hidden / known non-build dirs
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS
                       and not d.startswith('.')]
            for f in files:
                fpath = os.path.join(root, f)
                ext = os.path.splitext(f)[1].lower()

                # On Windows, look for .exe; on POSIX check exec bit
                if sys.platform == 'win32':
                    if ext not in ('.exe',):
                        continue
                else:
                    if ext in ('.o', '.a', '.so', '.dylib', '.py', '.sh',
                               '.h', '.c', '.cpp', '.md', '.txt', '.json',
                               '.cmake', '.yml', '.yaml', '.toml', '.rs'):
                        continue
                    if not os.access(fpath, os.X_OK):
                        continue

                real = os.path.realpath(fpath)
                if real in seen:
                    continue
                seen.add(real)

                is_test = bool(test_patterns.search(f))
                if test_only and not is_test:
                    continue

                try:
                    size = os.path.getsize(fpath)
                except OSError:
                    size = 0

                results.append({
                    'path': fpath,
                    'name': f,
                    'size': size,
                    'is_test': is_test,
                })

    # Sort: name_hint matches first, then by size (larger = more likely real binary)
    def _sort_key(item):
        hint_match = 0
        if name_hint and name_hint.lower() in item['name'].lower():
            hint_match = -1  # sort first
        return (hint_match, -item['size'])

    results.sort(key=_sort_key)
    return results


REPO_DOC_FILES = [
    'README.md', 'readme.md', 'README.rst', 'README',
    'BUILD.md', 'BUILDING.md',
    'CONTRIBUTING.md', 'DEVELOPMENT.md',
    'docs/BUILD.md', 'docs/Development.md', 'docs/building.md',
    '.github/copilot-instructions.md',
]


def scan_repo_context(repo_root: str) -> dict:
    """Scan a repository root and return context useful for debugging.

    Returns:
        {
            'repo_root':     absolute path,
            'build_system':  result of detect_build_system() or None,
            'doc_files':     list of found documentation file paths,
            'build_hints':   extracted build instructions (first 200 lines of each doc),
            'source_dirs':   list of directories containing C/C++ source files,
            'has_tests':     bool -- whether a test directory was found,
            'test_dirs':     list of directories likely containing test code,
        }
    """
    repo = os.path.abspath(repo_root)

    # Build system
    build_sys = detect_build_system(repo)

    # Documentation files
    found_docs = []
    build_hints = []
    for doc_rel in REPO_DOC_FILES:
        doc_path = os.path.join(repo, doc_rel)
        if os.path.isfile(doc_path):
            found_docs.append(doc_rel)
            # Extract build-related lines
            try:
                with open(doc_path, 'r', encoding='utf-8', errors='replace') as fh:
                    lines = fh.readlines()[:200]
                    for line in lines:
                        ll = line.lower()
                        if any(kw in ll for kw in (
                            'build', 'compile', 'cmake', 'make',
                            'debug', 'install', 'prerequisite', 'depend',
                        )):
                            build_hints.append(line.rstrip())
            except OSError:
                pass

    # Source & test directories
    source_dirs = []
    test_dirs = []
    test_names = {'test', 'tests', 'testing', 'test_', 'spec', 'specs'}
    src_names = {'src', 'source', 'lib', 'core'}

    for entry in os.listdir(repo):
        full = os.path.join(repo, entry)
        if not os.path.isdir(full) or entry.startswith('.'):
            continue
        low = entry.lower()
        if low in test_names or low.startswith('test'):
            test_dirs.append(entry)
        if low in src_names or low == 'src':
            source_dirs.append(entry)

    # Also detect nested src/core/test patterns (one level deep)
    for top in ('src', 'lib'):
        parent = os.path.join(repo, top)
        if os.path.isdir(parent):
            for entry in os.listdir(parent):
                full = os.path.join(parent, entry)
                if os.path.isdir(full) and entry.lower() in test_names:
                    test_dirs.append(os.path.join(top, entry))
                if os.path.isdir(full) and entry.lower() in src_names:
                    source_dirs.append(os.path.join(top, entry))

    return {
        'repo_root': repo,
        'build_system': build_sys,
        'doc_files': found_docs,
        'build_hints': build_hints[:50],  # cap for JSON output size
        'source_dirs': source_dirs or ['src', '.'],
        'has_tests': len(test_dirs) > 0,
        'test_dirs': test_dirs,
    }


def compile_source(
    source_file: str,
    output: Optional[str] = None,
    compiler_info: Optional[dict] = None,
    extra_flags: Optional[List[str]] = None,
) -> Tuple[str, str]:
    """Compile a C/C++ source file with debug symbols.

    Args:
        source_file: Path to .c / .cpp file.
        output: Optional path for the output executable.
        compiler_info: A dict from ToolchainInfo.compilers (or recommend()).
        extra_flags: Extra compiler flags.

    Returns:
        (executable_path, human_message)
    """
    if not os.path.isfile(source_file):
        raise FileNotFoundError(f'Source file not found: {source_file}')

    src = os.path.abspath(source_file)
    ext = os.path.splitext(src)[1].lower()
    base = os.path.splitext(src)[0]

    if ext not in SOURCE_EXTENSIONS:
        raise ValueError(f'Not a C/C++ source file: {source_file}')

    if output is None:
        output = base + ('.exe' if sys.platform == 'win32' else '')

    # Auto-detect compiler if not specified
    if compiler_info is None:
        toolchain = ToolchainInfo()
        rec = toolchain.recommend(ext)
        if rec['compiler'] is None:
            raise FileNotFoundError(
                'No C/C++ compiler found.\n' + rec['note']
            )
        compiler_info = rec['compiler']

    cc_path = compiler_info['path']
    cc_name = compiler_info['name']

    # Build the compile command
    if cc_name == 'msvc':
        cmd = [cc_path, '/nologo', '/Zi', '/Od', '/Fe:' + output, src]
        if ext in CPP_EXTENSIONS:
            cmd.insert(1, '/EHsc')
    else:
        # GCC / Clang family
        cmd = [cc_path, '-g', '-O0', '-o', output, src]

    if extra_flags:
        cmd.extend(extra_flags)

    print(f'Compiling: {" ".join(cmd)}')

    env = None
    # On Windows, clang/clang++ need the MSVC linker (link.exe).
    # If link.exe isn't on PATH, use vcvarsall.bat to set up the environment.
    if sys.platform == 'win32' and cc_name in ('clang', 'clang++'):
        if not shutil.which('link.exe'):
            vs_path = ToolchainInfo._find_vs_install_path()
            if vs_path:
                arch = 'x64' if platform.machine().lower() in (
                    'amd64', 'x86_64', 'x64',
                ) else 'x86'
                vcvarsall = os.path.join(
                    vs_path, 'VC', 'Auxiliary', 'Build', 'vcvarsall.bat',
                )
                if os.path.isfile(vcvarsall):
                    try:
                        r = subprocess.run(
                            f'cmd /c ""{vcvarsall}" {arch} && set"',
                            capture_output=True, text=True, timeout=30,
                            shell=True,
                        )
                        if r.returncode == 0:
                            env = {}
                            for line in r.stdout.split('\n'):
                                line = line.strip()
                                if '=' in line:
                                    k, v = line.split('=', 1)
                                    env[k] = v
                            print('  (Using Visual Studio environment for linking)')
                    except Exception:
                        pass

    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=120, env=env,
    )

    if result.returncode != 0:
        error_msg = (result.stderr or result.stdout)[:4000]
        raise RuntimeError(f'Compilation failed (exit {result.returncode}):\n{error_msg}')

    msg = f'Compiled {os.path.basename(src)} -> {os.path.basename(output)} using {cc_name}'
    if result.stderr.strip():
        msg += f'\nWarnings:\n{result.stderr[:2000]}'

    return (os.path.abspath(output), msg)

class CppDebugServer(BaseDebugServer):
    LANGUAGE = "C/C++"
    SCRIPT_NAME = "cpp_debug_session.py"


def find_debugger(preference: Optional[str] = None) -> Tuple[str, str]:
    """Find an available debugger. Returns (type, path).

    Uses ToolchainInfo for robust platform-aware detection.
    Validates that found debugger binaries actually work (no missing
    DLLs, no crashes). Searches Visual Studio directories and Windows
    SDK paths on Windows.
    type is 'gdb', 'lldb', or 'cdb'.
    """
    # Use ToolchainInfo which searches VS paths and validates tools
    toolchain = ToolchainInfo()

    if preference:
        pref = preference.lower()
        if pref not in ('gdb', 'lldb', 'cdb'):
            raise ValueError(f"Unknown debugger: {pref}. Use 'gdb', 'lldb', or 'cdb'.")
        # Check if ToolchainInfo already found and validated it
        dbg = next(
            (d for d in toolchain.debuggers if d['name'] == pref), None
        )
        if dbg:
            return (dbg['name'], dbg['path'])
        # Fallback: try PATH directly with validation
        path = shutil.which(pref)
        if not path and pref == 'cdb':
            # Store-installed WinDbg uses arch-suffixed names
            arch = platform.machine().lower()
            if arch in ('amd64', 'x86_64', 'x64'):
                path = shutil.which('cdbX64')
            elif arch in ('arm64', 'aarch64'):
                path = shutil.which('cdbARM64')
        if path:
            vflag = '-version' if pref == 'cdb' else '--version'
            if ToolchainInfo._validate_tool(path, version_flag=vflag):
                return (pref, path)
        raise FileNotFoundError(
            f"{pref.upper()} not found or not working.\n"
            + toolchain._install_instructions()
        )

    if toolchain.debuggers:
        # On Windows prefer CDB (native PDB support), then GDB, LLDB
        if sys.platform == 'win32':
            order = ('cdb', 'gdb', 'lldb')
        else:
            order = ('gdb', 'lldb')
        for name in order:
            dbg = next(
                (d for d in toolchain.debuggers if d['name'] == name), None
            )
            if dbg:
                return (dbg['name'], dbg['path'])

    raise FileNotFoundError(
        'No working C/C++ debugger found.\n'
        + toolchain._install_instructions()
    )


def create_debugger(executable: str, debugger_type: str, debugger_path: str,
                    source_paths: Optional[List[str]] = None,
                    attach_pid: Optional[int] = None,
                    core_dump: Optional[str] = None,
                    program_args: Optional[str] = None):
    kwargs = dict(
        source_paths=source_paths,
        attach_pid=attach_pid,
        core_dump=core_dump,
        program_args=program_args,
    )
    if debugger_type == 'gdb':
        return GdbDebugger(executable, debugger_path, **kwargs)
    elif debugger_type == 'lldb':
        return LldbDebugger(executable, debugger_path, **kwargs)
    elif debugger_type == 'cdb':
        return CdbDebugger(executable, debugger_path, **kwargs)
    else:
        raise ValueError(f"Unknown debugger type: {debugger_type}")
