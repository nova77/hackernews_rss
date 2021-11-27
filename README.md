# Dockerized python app to create RSS feeds for hackernews entries

Creates a docker service that generates a feed of articles from the hackernews rss feed.

It uses [readability](https://pypi.org/project/readability/) to extract the content from the pages, but can also access [Full-Text-RSS](https://www.fivefilters.org/full-text-rss) ([local](https://github.com/heussd/fivefilters-full-text-rss-docker) or otherwise) for specific sites.
It also includes redis for caching.

## Setup

Copy `.env-dist` to `.env` and change the configuration.
Most of the times it's just about `FLASK_PORT`, `FULLTEXT_RSS_URL`, and `LOG_PATH`.

Start the service with:

```bash
docker-compose build
docker-compose up -d
```

## Usage

The rss feed will be available at:

```url
http://localhost:5000/hnrss.org/newest
```

If you want to only pick the ones with high votes you can specify it in the
HN url, eg.

```url
http://localhost:5000/hnrss.org/newest?points=50
```

## Notes

* Not every url is parsed. For instance PDFs and twits will be marked as such
and passed through.
* `Article URL` and `Comments URL` are appended at the end of the parsed article
so that the HN discussion is still accessible.
