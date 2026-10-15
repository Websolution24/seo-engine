# SEO Engine

Un petit moteur SEO en Python, proposé par [WebSolution24](https://www.websolution24.fr/).

Il permet d'extraire les éléments HTML d'une liste de pages, de collecter des résultats de recherche avec DataForSEO et, en option, de préparer des suggestions éditoriales avec OpenAI. Il s'exécute sur votre ordinateur, sans interface web ni serveur à héberger.

| Module | Fonction | API payante |
| --- | --- | --- |
| `analyze_urls.py` | Extrait title, description, premier H1, quinze premiers H2, fil d'Ariane reconnu et expressions pondérées | Aucune |
| `analyze_serp.py` | Collecte volumes, CPC, résultats organiques, pack local, questions et recherches associées | DataForSEO |
| `analyze_openai.py` | Suggère intention, format, angle et thèmes à partir du contexte SERP ; exporte vers Excel | OpenAI |

Le code est accessible sans abonnement Semrush. Les fournisseurs d'API restent des services distincts, avec leurs propres tarifs. Aucun identifiant n'est fourni dans ce dépôt.

## Installation

Avec Python 3.11 ou supérieur, depuis le dossier téléchargé :

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Sur macOS/Linux, activer l'environnement avec `source .venv/bin/activate`.

## Essayer l'analyse d'URL, sans clé API

```text
python analyze_urls.py --input examples/urls.txt
```

L'exemple contient une page publique WebSolution24. Pour analyser vos propres pages, créer un fichier texte avec une URL par ligne et passer son chemin à `--input`.

Résultat : `output/seo_engine_output.xlsx`, onglet `urls_analysis`. Les colonnes contiennent notamment l'URL, le domaine, les champs extraits, les expressions pondérées et le statut `ok` ou `fetch_error`.

Les nombres de « structural_importance » décrivent la présence de champs et leur pondération dans le code. Ils ne mesurent ni leur pertinence, ni la demande de recherche, ni une qualité SEO globale.

## Activer les modules API

Ces commandes consomment les crédits de vos comptes fournisseurs. Elles exigent le paramètre explicite `--allow-paid-api`.

1. Copier `.env.example` vers `.env` et y renseigner uniquement vos propres identifiants.
2. Préparer un fichier de requêtes, une par ligne. `examples/keywords.txt` illustre le format.
3. Lancer les étapes voulues :

```text
python analyze_serp.py --input examples/keywords.txt --write-bridge --allow-paid-api
python analyze_openai.py --allow-paid-api
python analyze_urls.py
```

L'analyse d'URL est indépendante des deux autres modules. Dans le pipeline complet, la lancer en dernier : l'étape OpenAI recrée le classeur Excel et réinitialise l'onglet `urls_analysis`. Si `input/urls.txt` existe, il est prioritaire sur la liste générée par `--write-bridge`.

Variables reconnues : `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD`, `SEO_ENGINE_LANGUAGE_CODE`, `SEO_ENGINE_LOCATION_CODE`, `OPENAI_API_KEY`, `OPENAI_MODEL`. Les valeurs facultatives ont leurs défauts dans `.env.example`. La configuration `.env` est recherchée uniquement dans ce projet ; elle est exclue du suivi Git.

La collecte produit `output/serp_raw.json` et `output/run_meta.json`, puis éventuellement `output/urls_from_serp.txt`. Elle demande les volumes par lot et une SERP par requête ; des échecs peuvent provoquer jusqu'à trois tentatives. L'étape OpenAI transmet le contexte SERP et réalise une demande par entrée exploitable.

## Bien interpréter les résultats

- Le HTML est analysé sans rendu JavaScript. Le code n'effectue pas un audit complet, un contrôle d'indexation ou une mesure de performance.
- `rank_absolute` et `rank_group` distinguent les positions absolue et dans le groupe organique lorsqu'elles sont fournies. Le champ historique `rank` est conservé pour compatibilité.
- Les suggestions IA utilisent les titres, domaines, questions et autres éléments SERP ; elles ne reposent pas sur la lecture intégrale des pages concurrentes.
- `experimental_difficulty_proxy` et `experimental_opportunity_proxy` sont des heuristiques historiques. La première utilise notamment la concurrence **publicitaire** Google Ads : ce n'est pas une difficulté de référencement naturel validée. [Définition DataForSEO](https://docs.dataforseo.com/v3/keywords_data-google_ads-search_volume-live/).
- Vérifier les statuts `serp_error`, `ia_error` et `fetch_error`. Conserver les sorties utiles avant une nouvelle exécution : certains fichiers sont remplacés.
- Les chaînes exportées pouvant être interprétées comme des formules sont neutralisées. Entrées personnelles, sorties, journaux et configurations ne font pas partie du dépôt.

## Vérifications locales

```text
python -m unittest discover -s tests -v
```

Les tests utilisent des données synthétiques et ne contactent aucune API payante. Ils ne certifient pas la disponibilité actuelle des fournisseurs.

## Origine de cette version

Version publique préparée à partir du moteur Python WebSolution24 : documentation, dépendances, exemples, garde explicite pour les API payantes et sécurisation des exports ajoutés. Le moteur d'origine reste séparé. Aucune interface n'est requise pour l'utiliser.

Projet proposé par [WebSolution24](https://www.websolution24.fr/). Aucune licence de réutilisation supplémentaire n'est attribuée à cette version.
