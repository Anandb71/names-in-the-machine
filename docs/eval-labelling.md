# How the free-text eval is labelled

The eval is every unique free-text credit line that `nitm scan` finds on the machine, plus
about 40 real negatives (license template text and credit sentences that name no one).
`nitm eval build` writes them to `eval/lines.jsonl`. Email addresses are already removed,
exactly as they are before Gemma reads a line.

Labels are written **before** the scan sends any of these lines to Gemma. `nitm eval freeze`
then records the sha256 of the lines and the labels, and `nitm eval run` refuses to score if
either file changed after that. (In the first run, on 27 Oct 2026, the three labelling passes
were AI agents that saw only this guide and the raw lines.)

## The rules

Each line gets the list of real people it names, copied exactly as written in the line.

1. A label is a full personal name as written: at least two words, normally a given name
   (or initials) and a family name. "Ian Murdock", "D.J. Bernard", "ISHIDA Mutsuki",
   "Fabian M. Di Nardo", "Kasper S. KELLERMAN". Copy it character for character, with middle
   initials, particles and capitals. Leave off a possessive 's ("Jon Gilfoyle's" is
   labelled "Jon Gilfoyle") and trailing punctuation.
2. Only real, individual people. Not companies, projects, teams, universities, foundations,
   mailing lists, products or places ("Free Software Foundation", "Debian GCC Maintainers",
   "Sun Microsystems", "Stichting Mathematisch Centrum").
3. People who never lived are not labels: the GPL's example people (James Hacker, Ty Coon)
   and its example program (Gnomovision).
4. Skip single-word mentions: a lone surname ("Bernard"), a lone given name, a handle
   ("mossberry"), or a given name followed only by an initial ("Andrew M.").
5. A pseudonym written like a name that stands for one person counts ("Pale Architect").
6. A name that appears twice in a line is labelled once.
7. Names inside URLs are not labels.
8. Every role counts: author, maintainer, Debian packager, contributor, or the person in an
   X11 "the name of X shall not be used" clause. Whether the person is alive or dead does
   not matter here.
9. If unsure whether a string is one human's name, ask how a reader of the line would take
   it. Three labellers work independently; a name stays in the gold set when at least two
   of them agree, and every disagreement is decided by hand and noted.

## Why the labelled files are not in the repo

The lines and labels name hundreds of living contributors. The tool never lists living
people, so `eval/` stays on the machine. The run log keeps the counts, the scores and the
sha256 of both files.
