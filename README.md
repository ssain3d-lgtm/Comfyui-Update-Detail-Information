# ComfyUI Update Detailcheck

**See exactly what changed the last time you updated ComfyUI.**

ComfyUI updates are silent. `git pull` runs, ComfyUI-Manager updates a hundred node
packs, pip quietly moves `numpy` half a version — and all you get is a console that
scrolls past. When something breaks an hour later, there is no record of what moved.

This adds one line to your update script. After every update you get:

```
::::::::::: ComfyUI update report (2026-08-27 17:18) :::::::::::
  core        30d03fea -> d8e7bbc9  (4 commits)
  nodes       changed 2 / added 1 / removed 1
  packages    changed 2  [!] av 16.0.1 -> 17.0.1
  report      ...\_update-report\reports\2026-08-27_1718.html
```

…and a full HTML page that opens in your browser: every core commit with its subject
and date, every node that moved with a GitHub compare link, every package version
change, with the ones that habitually break ComfyUI flagged.

**Nothing has to run before an update.** The previous report's snapshot is the baseline.

---

## Install

1. Download this repository ([ZIP](https://github.com/ssain3d-lgtm/Comfyui-Update-Detailcheck/archive/refs/heads/main.zip)).
2. Copy **`Update-Report.bat`** and the **`_update-report`** folder into your ComfyUI
   install — the folder that contains `ComfyUI\` and `python_embeded\`:

   ```
   ComfyUI_windows_portable\        <- put them here
     ComfyUI\
     python_embeded\
     update\
     Update-Report.bat             <- copied
     _update-report\               <- copied
   ```

3. Double-click **`Update-Report.bat`** once. It saves a baseline and tells you so.
4. *(optional, recommended)* Double-click **`_update-report\reinject.bat`** to hook the
   report into your update scripts, so it runs by itself after every update.

That's it. Update ComfyUI as you always do; the report shows up when it finishes.

### Supported layouts

Auto-detected, no configuration:

| Layout | Python it finds |
|---|---|
| ComfyUI portable (`ComfyUI_windows_portable`) | `python_embeded\python.exe` |
| [ComfyUI-Easy-Install](https://github.com/ivo-toby/ComfyUI-Easy-Install) | `python_embeded\python.exe` |
| venv install | `venv\` / `.venv\` / `ComfyUI\venv\` |
| anything else | `python` on `PATH` |

Requires **Python 3.8+** and **git** on `PATH`. Python 3.11+ parses `pyproject.toml`
properly; older versions fall back to a regex and work fine.

### Linux / macOS

The `.bat` hook is Windows-only, but the report itself is not. Run it by hand after
updating:

```sh
./update-report.sh              # or: python3 _update-report/update_report.py
```

---

## What it tracks

| Thing | How | Shown as |
|---|---|---|
| ComfyUI core | git `HEAD` | `git log old..new` — date, sha, subject + GitHub compare link |
| git-installed nodes | git `HEAD` | same (capped at 20 commits, rest counted) |
| registry (cnr) nodes | `version` in `pyproject.toml` | `1.27.4 → 1.28.0` + repo link |
| new / removed nodes | folder listing | their own section |
| Python packages | `importlib.metadata` | every version change |

### Risky packages

These get pulled out and flagged, because when one of them shifts under you ComfyUI
usually stops starting:

`av` · `numpy` · `scipy` · `numba` · `llvmlite` · `torch` · `torchvision` ·
`torchaudio` · `torchsde` · `torchcodec` · `xformers` · `opencv-python*` ·
`transformers` · `diffusers` · `safetensors` · `pillow`

That list exists because of a real incident: an update script kept silently reinstalling
`av==16.0.1` while ComfyUI needed `17.0.1`, and the only symptom was an `ImportError` on
startup with no clue which update caused it.

---

## Usage

```
Update-Report.bat [options]

  --comfy PATH   path to the ComfyUI directory (auto-detected otherwise)
  --baseline     overwrite the snapshot without reporting
  --no-open      don't open the HTML report in a browser
  --no-color     plain console output
  --no-pause     don't wait for a keypress (used by the hook)
  --reinject     re-add the hook to your update scripts
  --help         show this
```

Reports pile up in `_update-report\reports\` as `YYYY-MM-DD_HHMM.html`. They're a few KB
each; delete them whenever.

---

## How the hook survives

`reinject.bat` adds two lines to your update script, just above the point where ComfyUI
starts:

```bat
:: ---- ComfyUI update report ----
if exist "%~dp0Update-Report.bat" call "%~dp0Update-Report.bat" --no-pause
```

The original is backed up next to it as `*.bak-hook`.

Some distributions rewrite their own update scripts when they self-update
(ComfyUI-Easy-Install does), which drops those lines. **Run `reinject.bat` again** — it's
idempotent, and `Update-Report.bat` keeps working by hand regardless.

Scripts that end in `:label` subroutines are skipped rather than patched, since appending
there would put the hook inside a subroutine. When a root-level updater exists, the
scripts it calls are left alone so the report doesn't run twice.

---

## Notes and limits

- **First run only saves a baseline.** Real diffs start with the second run.
- Merge commits are hidden (`--no-merges`). Every real change still appears.
- If a repo was force-pushed or shallow-cloned, the commit list may be unavailable —
  the report says so and still shows the sha transition.
- `state.json` holds your node list and installed package versions. It stays local; it's
  gitignored here.
- Reading is all it does: `git rev-parse`, `git log`, and file reads. It never writes to
  your repos. The only files it modifies are the update `.bat` files you point
  `reinject.bat` at, and those are backed up first.

## Tests

```
python _update-report/test_update_report.py     # 73 tests
python _update-report/test_inject_hook.py       # 27 tests
```

No dependencies — standard library only.

---
---

# 한국어

## 이게 뭔가요

ComfyUI를 업데이트하면 뭐가 바뀌었는지 알 수가 없습니다. `git pull` 돌아가고, 노드
수십 개가 갱신되고, pip가 조용히 `numpy` 버전을 바꿔놓고, 콘솔은 그냥 지나갑니다.
한 시간 뒤에 뭔가 깨져도 **무엇이 움직였는지 남은 기록이 없습니다.**

이 도구는 업데이트 스크립트에 한 줄을 넣어서, 업데이트가 끝나면 자동으로 변경내역을
보여줍니다. 콘솔에는 요약 세 줄, 브라우저에는 상세 페이지 — 코어 커밋 전체(날짜·SHA·제목),
바뀐 노드마다 GitHub compare 링크, 패키지 버전 변화까지.

**업데이트 전에 미리 실행해둘 필요가 없습니다.** 직전 리포트의 스냅샷이 기준점입니다.

## 설치

1. 이 저장소를 [ZIP으로 받습니다](https://github.com/ssain3d-lgtm/Comfyui-Update-Detailcheck/archive/refs/heads/main.zip).
2. **`Update-Report.bat`** 과 **`_update-report`** 폴더를 ComfyUI 설치 폴더
   (`ComfyUI\` 와 `python_embeded\` 가 들어있는 그 폴더)에 복사합니다.
3. **`Update-Report.bat`** 을 한 번 더블클릭 → 기준점이 저장됩니다.
4. *(권장)* **`_update-report\reinject.bat`** 을 더블클릭 → 업데이트 스크립트에 훅이
   들어가서, 이후로는 업데이트가 끝날 때마다 알아서 뜹니다.

포터블 / Easy-Install / venv 설치를 알아서 구분하고, 파이썬도 알아서 찾습니다.
`git` 이 PATH에 있어야 합니다. 리눅스·맥은 `.bat` 훅 대신 `update-report.sh` 를 직접 실행하세요.

## 추적 대상

| 대상 | 방식 | 표시 |
|---|---|---|
| ComfyUI 코어 | git `HEAD` | `git log old..new` 커밋 목록 + compare 링크 |
| git 설치 노드 | git `HEAD` | 동일 (커밋 20개 초과분은 개수만) |
| 레지스트리(cnr) 노드 | `pyproject.toml` 의 `version` | `1.27.4 → 1.28.0` |
| 신규 / 제거 노드 | 폴더 목록 비교 | 별도 섹션 |
| Python 패키지 | `importlib.metadata` | 버전 변경 전체 |

`av` `numpy` `scipy` `torch*` `opencv*` `numba` 같은 **위험 패키지**는 따로 강조합니다.
업데이트 스크립트가 `av` 를 16.0.1로 계속 되돌려놓는 바람에 ComfyUI가 `ImportError` 로
안 뜨는데 어느 업데이트가 원인인지 알 수 없었던 실제 사고에서 나온 목록입니다.

## 훅이 사라졌을 때

ComfyUI-Easy-Install 처럼 자기 업데이트 스크립트를 통째로 덮어쓰는 배포판이 있습니다.
그러면 넣어둔 훅 두 줄도 같이 날아갑니다. → **`reinject.bat` 을 다시 실행**하면 됩니다
(몇 번을 돌려도 중복되지 않습니다). 원본은 `*.bak-hook` 으로 백업됩니다.
훅이 없어도 `Update-Report.bat` 수동 실행은 항상 됩니다.

## 알아둘 점

- **첫 실행은 기준점만 저장**합니다. 실제 변경내역은 두 번째 실행부터 나옵니다.
- 머지 커밋은 숨깁니다(`--no-merges`). 실제 변경 커밋은 다 나옵니다.
- 강제 푸시나 얕은 클론이면 커밋 목록을 못 가져올 수 있습니다. 그때는 SHA 전환만 표시합니다.
- `state.json` 에는 노드 목록과 설치 패키지 버전이 들어갑니다. 로컬에만 남고 gitignore 됩니다.
- 읽기만 합니다. 저장소에 쓰지 않습니다. 수정하는 파일은 `reinject.bat` 이 훅을 넣는
  업데이트 `.bat` 뿐이고, 그것도 먼저 백업합니다.
