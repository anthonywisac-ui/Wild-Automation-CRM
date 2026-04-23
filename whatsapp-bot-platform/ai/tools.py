def create_new_feature(file, code):
    path = f"ai/generated/{file}"

    with open(path, "w") as f:
        f.write(code)

    return f"{file} created safely"