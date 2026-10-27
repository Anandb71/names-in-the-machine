# The Wikidata snapshot

Source: Wikidata, https://www.wikidata.org. License: CC0 1.0 (Wikidata structured data).
Retrieved from `https://query.wikidata.org/sparql` between 2026-10-27T17:31:07+05:30 and 2026-10-27T17:50:19+05:30,
with `nitm snapshot fetch`. The tool reads these files offline and never queries Wikidata at run time.

- `deceased.json`: humans with a death date (P570) who have a software or computer-science occupation, or who
  are linked as developer, creator, author, programmer or designer of a software item. For each: label,
  aliases, birth and death dates with their precision and rank, occupations, and linked items.
- `no_death_date.txt`: people with those occupations or links and no death date, stored only as short
  hashes of their name keys, so this repository carries no list of living people's names.

## Counts

| | |
|---|---|
| occupations | 330 |
| deceased by occupation | 2,923 |
| deceased by software link | 333 |
| people | 3,110 |
| people human | 3,104 |
| no death date by occupation | 48,121 |
| no death date people | 51,889 |
| no death date name hashes | 60,416 |
| queries | 288 |
| rows fetched | 190,914 |
| failed attempts | 5 |

Not requested: cause of death (P509), manner of death (P1196), place of death (P20) and every other property not listed in these queries.

Incomplete parts: none.

## Queries

%(roots)s, %(occs)s and %(qids)s are VALUES lists: the occupation roots, the occupation closure in chunks of 60, and person QIDs in batches (150 for names/facts/dates/links out, 100 for links in, 300 for link labels, 400 for no-death-date names). %(prop)s is each link property except those in link_properties_per_person_only. Failing batches were split in half; a failing single occupation was paged with ORDER BY ?p LIMIT/OFFSET.

Occupation roots: Q5482740 (programmer), Q82594 (computer scientist), Q1709010 (software engineer), Q183888 (software developer).
Link properties: P178 (developer), P170 (creator), P50 (author), P943 (programmer), P287 (designed by), P112 (founded by), P61 (discoverer or inventor).

### occupations

```sparql
SELECT DISTINCT ?occ ?occLabel WHERE {
  VALUES ?root { %(roots)s }
  ?occ wdt:P279* ?root .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
```

### deceased_by_occupation

```sparql
SELECT DISTINCT ?p WHERE {
  VALUES ?occ { %(occs)s }
  ?p wdt:P106 ?occ ; wdt:P570 ?died .
}
```

### deceased_by_link

```sparql
SELECT DISTINCT ?p ?sw WHERE {
  ?sw wdt:%(prop)s ?p .
  ?p wdt:P31 wd:Q5 ; wdt:P570 ?died .
  ?sw wdt:P31/wdt:P279* wd:Q7397 .
}
```

### no_death_by_occupation

```sparql
SELECT DISTINCT ?p WHERE {
  VALUES ?occ { %(occs)s }
  ?p wdt:P106 ?occ ; wdt:P31 wd:Q5 .
  FILTER NOT EXISTS { ?p wdt:P570 ?died }
}
```

### no_death_by_link

```sparql
SELECT DISTINCT ?p WHERE {
  ?sw wdt:%(prop)s ?p .
  ?p wdt:P31 wd:Q5 .
  FILTER NOT EXISTS { ?p wdt:P570 ?died }
  ?sw wdt:P31/wdt:P279* wd:Q7397 .
}
```

### names

```sparql
SELECT ?p ?kind ?name WHERE {
  VALUES ?p { %(qids)s }
  VALUES ?kind { rdfs:label skos:altLabel }
  ?p ?kind ?name .
  FILTER(LANG(?name) IN ("en", "mul"))
}
```

### facts

```sparql
SELECT ?p ?prop ?value ?valueLabel WHERE {
  VALUES ?p { %(qids)s }
  VALUES ?prop { wdt:P31 wdt:P106 }
  ?p ?prop ?value .
  OPTIONAL { ?value rdfs:label ?valueLabel . FILTER(LANG(?valueLabel) = "en") }
}
```

### dates

```sparql
SELECT ?p ?which ?time ?precision ?rank WHERE {
  VALUES ?p { %(qids)s }
  VALUES (?which ?claim ?valueProp) { ("born" p:P569 psv:P569) ("died" p:P570 psv:P570) }
  ?p ?claim ?statement .
  ?statement wikibase:rank ?rank .
  OPTIONAL { ?statement ?valueProp ?node . ?node wikibase:timeValue ?time ; wikibase:timePrecision ?precision . }
}
```

### links_in

```sparql
SELECT ?p ?prop ?sw WHERE {
  VALUES ?p { %(qids)s }
  VALUES ?prop { wdt:P178 wdt:P170 wdt:P943 wdt:P287 wdt:P112 wdt:P61 }
  ?sw ?prop ?p .
}
```

### links_out

```sparql
SELECT ?p ?sw WHERE {
  VALUES ?p { %(qids)s }
  ?p wdt:P800 ?sw .
}
```

### link_labels

```sparql
SELECT ?sw ?swLabel WHERE {
  VALUES ?sw { %(qids)s }
  ?sw rdfs:label ?swLabel . FILTER(LANG(?swLabel) = "en")
}
```

