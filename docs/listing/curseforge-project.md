# CurseForge project

- Project: NeedleDropper. Created 2026-11-05 by the owner and awaiting CurseForge approval.
- **Project ID: 1786392**. This goes into `addon/NeedleDropper/NeedleDropper.toc` as `## X-Curse-Project-ID: 1786392`, along with `## Version: @project-version@`, after the v1.2 integration lands. Update the exact-TOC test in the same change.
- URL: https://www.curseforge.com/wow/addons/needledropper (shows a preview to the owner until approval)
- GitHub secret `CF_API_KEY` is set on Soupoleon/NeedleDropper (2026-11-05). The release workflow passes it to the BigWigs packager as `CF_API_TOKEN`.
- First upload: **Beta** (`v1.2.1-beta.1`, the first public version: 1.2.0 was never uploaded), after the in-game check in docs/FIRST-TEST.md. Game version: WoW Forever (packager v2.7.0 maps interface 16xxx to Forever).
- The TOC has `## X-Curse-Project-ID: 1786392` and a literal `## Version: 1.2.1` (the release workflow accepts a literal that matches the tag, or `@project-version@`).
