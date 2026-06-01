def calculate_histogram(number_stream, bucket_size):
    histogram = {}
    for number in number_stream:
        try:
            num = float(number)
            if bucket_size == 0:
                raise ValueError("Bucket size must be non-zero.")
            lower_bound = (num // bucket_size) * bucket_size
            upper_bound = lower_bound + bucket_size
            bucket = f"{lower_bound}-{upper_bound}"
            histogram[bucket] = histogram.get(bucket, 0) + 1
        except ValueError:
            continue
    return histogram

if __name__ == "__main__":
    print("Histogram builder no-op self-check.")
    exit(0)