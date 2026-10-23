"""Client-site preview of an approved draft, built by a Manus agent.

The agent lays out the draft word for word in the client's own site design and
delivers one self-contained ``index.html``. It is a review aid only: WordPress
still receives the clean draft content. Modules: ``brief`` (what the agent is
told), ``run`` (build, follow, collect, edit, stop, undo), ``events`` (progress and
confirmation policy), ``answer`` (questions), ``checks`` (review notes),
``images`` (safe image handling) and ``store`` (where runs live).
"""
