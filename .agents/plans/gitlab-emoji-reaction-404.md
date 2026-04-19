# GitLab Emoji Reaction 404 on MR Discussion Notes

## Status: Open — low priority

## Observed

During `sentinel execute DHLEXS_DHLEXC-383 --revise` (2026-04-19), the revision flow successfully replies to and resolves MR discussions, but the subsequent emoji reaction call fails with 404:

```
WARNING - Failed to add reaction: 404 Client Error: Not Found for url:
  https://gitlab.hosted-tools.com/api/v4/projects/dhl%2Fdhl_express/merge_requests/98/discussions/1335e5d03e4d058aebb5e14e94d35445950257a4/notes/652186/award_emoji

WARNING - Failed to add reaction: 404 Client Error: Not Found for url:
  https://gitlab.hosted-tools.com/api/v4/projects/dhl%2Fdhl_express/merge_requests/98/discussions/1ab20ca913dcf37af028b37b2e4f1fcb5a6b72ac/notes/652187/award_emoji
```

Both discussions were replied to and resolved successfully despite the reaction failure.

## Likely cause

The note IDs (652186, 652187) in the emoji URL appear to be the IDs of the **newly created reply notes**, not the original discussion notes. The award_emoji endpoint expects the note to already exist and be accessible. Possible issues:

1. **Timing**: The reply note was just created and the emoji call fires immediately — GitLab may not have the note indexed yet (unlikely but possible with eventual consistency).
2. **Wrong note ID**: The code may be targeting the reply note ID instead of the original discussion note ID. Award emoji should go on the **original note** the reviewer wrote, not on Sentinel's reply.
3. **URL encoding**: The project path `dhl%2Fdhl_express` is URL-encoded — this is correct for GitLab API, so unlikely the issue.

## Investigation steps

1. Check `base_developer.py` — find where `award_emoji` is called after replying to a discussion
2. Verify which note ID is used: the original discussion note or the newly created reply note
3. Check if GitLab's award_emoji endpoint works on discussion notes vs. regular MR notes (API docs: `POST /projects/:id/merge_requests/:mid/notes/:nid/award_emoji`)

## Impact

Cosmetic only. Discussions are replied to and resolved correctly. The emoji reaction (likely a checkmark or eyes) is just a visual indicator that Sentinel processed the note.
