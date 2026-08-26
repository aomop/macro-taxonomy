# Data provenance and reuse

The `LICENSE` file covers the **code** in this repository. It says nothing about
the **taxonomic data** the pipeline assembles, which comes from elsewhere. This
document explains where that data comes from and what terms apply to it.

---

## No claim of ownership

This project claims **no ownership** of the taxonomic data it compiles, and
imposes **no additional restrictions** on what anyone does with it.

The pipeline is a retrieval and reshaping tool. It queries public taxonomic
databases, flattens what they return into a table, and writes a CSV. The facts in
that table — scientific names, rank hierarchies, serial numbers, jurisdictional
occurrence, common names — originate with the sources below, not here. Whatever
terms those sources set are the terms that apply. Nothing in this repository adds
to them, and no permission from this project is needed to use, redistribute, or
modify the output.

---

## Sources

### ITIS — Integrated Taxonomic Information System

<https://www.itis.gov>

Supplies the taxonomic backbone: serial numbers (TSNs), parent/child hierarchy,
rank names, jurisdictional and geographic division records, and most common
names. ITIS is a partnership of United States, Canadian and Mexican agencies, and
states that its data are in the public domain. See the ITIS site for their
current terms and citation guidance — ITIS asks to be cited when its data is
used, which is worth honouring even where not strictly required.

A citation in the form ITIS requests looks like:

> Retrieved [DD Month YYYY], from the Integrated Taxonomic Information System
> (ITIS), <https://www.itis.gov>

Use the date the pipeline actually ran, which is encoded in the output filename
(`taxonomy_YYYYMMDD.csv`).

### iNaturalist

<https://www.inaturalist.org>

Used **only** as a fallback for English common names, for taxa where ITIS
returned none — a small minority of rows. No observation records, images,
locations, or user-contributed content of any other kind are retrieved or
redistributed.

iNaturalist content is governed by that site's own terms and licensing, which
vary by content type. Consult <https://www.inaturalist.org/pages/terms> before
redistributing the `common_names` column in a context where licensing matters.

---

## What this means in practice

- **The code** is MIT licensed. See [`LICENSE`](LICENSE).
- **The output data** is not this project's to license. Treat it as governed by
  the upstream sources above.
- **Attribution** should point at ITIS and iNaturalist, not at this repository.
  Crediting the pipeline is welcome but is not the attribution that matters.
- **If you redistribute the dataset**, carry this provenance information with it
  so the next person can trace it back.

Nothing here is legal advice. If your use has licensing consequences you care
about, read the upstream terms yourself rather than relying on this summary.

---

## Reproducing the dataset

Every field in the output is derived, not authored. Given the same seed TSN list
and the same upstream databases, the pipeline reproduces the same table — see the
main [README](README.md) for how to run it. Upstream records do change over time,
so a rebuild on a later date may legitimately differ from an earlier one; the
date in the filename is what identifies a given snapshot.
