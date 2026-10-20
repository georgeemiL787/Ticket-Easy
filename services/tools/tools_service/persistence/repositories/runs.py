from ..util import dump, now


def succeed(c, run, response):
    c.execute("UPDATE runs SET status='succeeded',result=?,completed_at=? WHERE id=?", (dump(response), now(), run))
