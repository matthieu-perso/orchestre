def MessageOK(data: any):
    return {"ok": "Success", "data": data}


def MessageErr(reason: str):
    return {"err": "Failure", "reason": reason}
