# PictoPy Issue #1485 — Fix Summary

## What Was Broken

You add a folder, PictoPy indexes the photos, and a memory gets built from them.
Later you delete that folder from Settings. The photos disappear from your library
— but the memory stays on the Memories page forever, showing **0 photos** and a
blank placeholder cover. Clicking **Regenerate** does nothing. Restarting the app
does nothing.

## Root Cause

A memory is stored in two places: a row in the `memories` table (the title, the
cover, the date), and one row per photo in a `memory_images` table linking them.

When the folder was deleted, the database correctly cascaded the deletion: the
folder's images went, and their `memory_images` rows went with them. But nothing
deletes the parent `memories` row — there is no direct link from a memory to a
folder, so the database has no way to know. The memory was left behind still
marked `complete`, and the listing query only ever checked that flag.

There was already a function written to handle exactly this —
`db_prune_empty_memories()` — with its own passing test. **Nothing in the
application ever called it.** It had been dead code since the day it was added.

Regenerate did not help because the only cleanup it ran, `db_delete_stale_memories()`,
looks for memories whose photos have *shifted dates*. It matches by inspecting the
photos a memory still holds, so a memory holding *zero* photos never matches.

## What We Changed

### Backend

1. **Gave the existing cleanup a caller.** A new `memory_curator_prune_empty()` in
   `memory_curator.py` wraps `db_prune_empty_memories()`, reads the user's own
   "minimum photos" setting instead of hardcoding a number, and never throws.

2. **Folder deletion now cleans up.** `DELETE /folders/delete-folders` runs the
   prune right after deleting the folder, before it replies. If the cleanup fails,
   it is logged and the folder deletion still succeeds.

3. **Disk rescans clean up too.** When files vanish from disk and a rescan drops
   their database rows, the same prune runs. This covers the case where no folder
   was ever deleted.

4. **Memory generation cleans up as a safety net.** Regenerate now prunes emptied
   memories before it builds anything.

5. **Belt and braces on the listing queries.** The three queries that show a memory
   to the user now skip any memory with no live photos *and* no live videos, so
   even if a cleanup were missed, an unrenderable card cannot reach the screen.
   Videos are deliberately included in that check so a memory that kept its clips
   is never hidden.

### Frontend

Deleting a folder already refreshed the folder list and face clusters, but not the
Memories list, so the grid kept serving cached cards. It now also invalidates the
shared `MEMORIES_QUERY_KEY`, which makes React Query refetch — and by then the
backend has already pruned.

## Tests Added

- **Folder deletion (end to end)** — delete a folder through the real API and
  confirm `folders`, `images` and `memory_images` are all empty and the memory no
  longer appears in `GET /memories`.
- **Other memories are untouched** — deleting one folder does not disturb a memory
  built from a different folder.
- **Deletion never fails over cleanup** — if pruning throws, the folder delete
  still returns 200.
- **Regeneration** — a curation run prunes emptied memories before it builds
  anything, uses the configured minimum rather than a magic number, and still
  produces its memories if the prune fails.
- **Partial deletion** — a memory that loses some photos but stays above the
  minimum survives and just shows a smaller count.
- **Boundary** — exactly at the minimum survives; one below is pruned.
- **User state preserved** — pruning does not clear viewed / dismissed / notified.
- **Video safety** — a memory whose photos were deleted but whose clips remain is
  still listed and still surfaceable.
- **Filesystem rescan** — files deleted from disk plus a rescan prunes the memory;
  a rescan that loses nothing prunes nothing.
- **Frontend** — successful folder deletion invalidates the Memories query.

## Result

```text
Folder deleted
→ source images deleted (database cascade)
→ memory_images rows cascade away
→ backend prunes the now-empty memory before replying
→ frontend invalidates the Memories query
→ Memories refetches
→ the orphaned memory is gone
```

The same happens when photos simply disappear from disk and PictoPy rescans.

## Files Changed

| File | What changed |
| --- | --- |
| `backend/app/utils/memory_curator.py` | Added `memory_curator_prune_empty()`, the single entry point every deletion path calls; the curation run now calls it too. |
| `backend/app/routes/folders.py` | `delete_folders` prunes emptied memories before replying, and logs rather than fails if that goes wrong. |
| `backend/app/utils/images.py` | Dropping images that vanished from disk now prunes the memories they emptied. |
| `backend/app/database/memories.py` | The listing, surfacing and unviewed-count queries skip memories with no live photos or videos. |
| `frontend/src/hooks/useFolderOperations.tsx` | Folder deletion now also invalidates the Memories query. |
| `backend/tests/test_folders.py` | End-to-end folder-deletion regression tests. |
| `backend/tests/test_memories_db.py` | Prune boundary, partial-deletion, user-state and video-safety tests. |
| `backend/tests/test_memory_curator.py` | Tests for the new prune entry point and its place in a curation run. |
| `backend/tests/test_images_rescan.py` | Regression test for photos vanishing from disk. |
| `frontend/src/hooks/__tests__/useFolderOperations.test.tsx` | Test that folder deletion invalidates the Memories query. |
