from datetime import datetime

def build_wildcard_filter(field_paths, values):
    """
    Builds a wildcard OR filter for multiple field paths and values.

    Args:
        field_paths (list): List of field paths from configuration.
        values (str): A comma-separated string of values to filter.

    Returns:
        dict: A bool query with should clauses for logical OR.
    """
    value_list = [val.strip() for val in values.split(",") if val.strip()]

    should_clauses = [
        {
            "wildcard": {
                field_path: {
                    "value": f"*{value}*",
                    "case_insensitive": True
                    }
                }
        }
        for value in value_list
        for field_path in field_paths
    ]

    return {
        "bool": {
            "should": should_clauses,
            "minimum_should_match": 1
        }
    }

def build_date_filter(begin_field=None, end_field=None, start_date=None, end_date=None):
    """
    Builds a string-based range filter for date fields supporting partial dates, 'null', 'not available; indisponible', and 'current'.

    Args:
        begin_field (str): Field name for the start date.
        end_field (str): Field name for the end date.
        start_date (str): The start date as a string.
        end_date (str): The end date as a string.

    Returns:
        list: A list of range queries for the provided date fields.
    """
    date_filters = []
    current_date = datetime.now().strftime('%Y-%m-%d')  # Current date in YYYY-MM-DD format

    # Handle start date for `gte`
    if start_date and start_date.lower() not in ['null', 'not available; indisponible']:
        if len(start_date) == 7:  # YYYY-MM
            start_date = f"{start_date}-01"  # Assume first day of the month
        elif len(start_date) == 4:  # YYYY
            start_date = f"{start_date}-01-01"  # Assume January 1st

        date_filters.append({
            "range": {
                begin_field: {
                    "gte": start_date
                }
            }
        })

    # Handle end date for `lte`
    if end_date:
        if end_date.lower() in ['null', 'not available; indisponible']:
            # Optionally exclude these
            date_filters.append({
                "term": {
                    end_field: "null"
                }
            })
        elif end_date.lower() == "present":
            # Treat 'present' as the current date and handle it separately
            end_date = current_date
            date_filters.append({
                "range": {
                    end_field: {
                        "lte": end_date
                    }
                }
            })

            """
            date_filters.append({
                "bool": {
                    "should": [
                        {
                            "range": {
                                end_field: {
                                    "lte": current_date  # Current date
                                }
                            }
                        },
                        {
                            "term": {
                                end_field: "Present"  # Exact "Present" match
                            }
                        }
                    ]
                }
            })
            """
        elif len(end_date) == 7:  # YYYY-MM
            end_date = f"{end_date}-31"  # Assume the last day of the month
        elif len(end_date) == 4:  # YYYY
            end_date = f"{end_date}-12-31"  # Assume December 31st

        # Avoid duplicate queries if "present" is handled
        if end_date.lower() != "present":
            date_filters.append({
                "range": {
                    end_field: {
                        "lte": end_date
                    }
                }
            })

    return date_filters

def build_spatial_filter(geo_field, bbox, relation=None):
    """
    Builds a spatial filter for geo_shape fields based on a bounding box (bbox).

    Args:
        geo_field (str): The geo_shape field name from the mapping configuration.
        bbox (list): A list of four coordinates defining the bounding box [min_lon, min_lat, max_lon, max_lat].
        relation (str): The spatial relation for the filter. Defaults to 'intersects'.

    Returns:
        dict: A geo_shape query using the 'envelope' type.

    Raises:
        ValueError: If the bbox is invalid or the relation is unsupported.
    """
    if relation is None:
        relation = "intersects"

    supported_relations = ["intersects", "disjoint", "within", "contains"]
    if relation not in supported_relations:
        raise ValueError(f"Unsupported relation '{relation}'. Must be one of {supported_relations}.")

    try:
        bbox = [float(val.strip()) for val in bbox.split(",") if val.strip()]
    except ValueError:
        raise ValueError("Invalid bbox format. Ensure it is a ',' separated string of numeric values.")    

    if not bbox or len(bbox) != 4:
        raise ValueError("Invalid bbox. Expected four coordinates: \n"
                         "min_lon (west) |min_lat (south) | max_lon (east) |max_lat (north)")

    min_lon, min_lat, max_lon, max_lat = bbox

    # Validate ranges
    if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180):
        raise ValueError("Longitude values must be between -180 and 180.")
    if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
        raise ValueError("Latitude values must be between -90 and 90.")

    return {
        "geo_shape": {
            geo_field: {
                "shape": {
                    "type": "envelope",
                    "coordinates": [[min_lon, max_lat], [max_lon, min_lat]]
                },
                "relation": relation
            }
        }
    }

def build_sort_filter(lang_filter, sort_field="relevancy", sort_order="desc"):
    """
    Builds a dynamic sort parameter for OpenSearch based on a single field and order.

    Args:
        sort_field (str): The field to sort by (e.g., "relevancy", "date", "popularity", "title").
                          Defaults to "relevancy" (maps to '_score') if not provided.
        sort_order (str): The sort order ("asc" for ascending, "desc" for descending).
                          Defaults to "desc". Forced to "desc" for relevancy.

    Returns:
        list: A valid sort parameter for OpenSearch.

    Raises:
        ValueError: If the provided sort_field is unsupported.
    """

    # Handle empty input strings by setting defaults
    if not sort_field:
        sort_field = "relevancy"  # Default sort field
    if not sort_order and sort_field == "title":
        sort_order = "asc"   # Makes for sense to have title sort alphabetically by default
    if not sort_order:
        sort_order = "desc"  # Otherwise, the default sort order is desc


    # Normalize input (case-insensitive)
    sort_field = sort_field.lower()
    sort_order = sort_order.lower()

    # Supported sort fields
    supported_sort_fields = ["_score", "date", "popularity", "title", "relevancy"]

    # Map user-friendly "relevancy" to OpenSearch's "_score"
    if sort_field == "relevancy":
        sort_field = "_score"

    # Validate the sort field
    if sort_field not in supported_sort_fields:
        raise ValueError(
            f"Unsupported sort field '{sort_field}'. Supported fields are: {supported_sort_fields}."
        )

    # Enforce descending order for '_score' (relevance)
    if sort_field == "_score":
        sort_order = "desc"

    # Adjust for specific OpenSearch requirements
    field_mapping = {}
    print("lang_filter", lang_filter)
    if lang_filter == "en":
        field_mapping = {
            "title": "title_en.keyword",  # Sort 'title' by its keyword version
            "date": "published"        # Map 'date' to 'published'
        }
    elif lang_filter == "fr":
        field_mapping = {
            "title": "title_fr.keyword",  # Sort 'title' by its keyword version
            "date": "published"        # Map 'date' to 'published'
        }
    
    sort_field = field_mapping.get(sort_field, sort_field)

    # Ensure valid sort order
    if sort_order not in ["asc", "desc"]:
        raise ValueError("Invalid sort order. Must be 'asc' or 'desc'.")

    # Return the sort parameter
    print(sort_field, " ", sort_order)
    return [{sort_field: {"order": sort_order}}]

def build_sort_filter2(sort_field="relevancy", sort_order="desc"):
    """
    Builds a dynamic sort parameter for OpenSearch based on a single field and order.

    Args:
        sort_field (str): The field to sort by (e.g., "relevancy", "date", "popularity", "title").
                          Defaults to "relevancy" (_score) if not provided.
        sort_order (str): The sort order ("asc" for ascending, "desc" for descending).
                          Defaults to "desc".

    Returns:
        list: A sort parameter for OpenSearch or an empty list if no sort field is specified.

    Raises:
        ValueError: If the provided sort_field is unsupported.
    """
    # Valid sort fields
    supported_sort_fields = ["_score", "date", "popularity", "title", "relevancy"]

    # Map user-friendly "relevancy" to "_score"
    if sort_field == "relevancy":
        sort_field = "_score"

    # Ensure the sort field is supported
    if sort_field not in supported_sort_fields:
        raise ValueError(
            f"Unsupported sort field '{sort_field}'. "
            f"Must be one of {supported_sort_fields}. Default is '_score' (relevancy)."
        )

    # Force relevance sorting to descending order
    if sort_field == "_score":
        sort_order = "desc"

    # OpenSearch doesn't like to sort 'text'. Make sure the 'keyword' instead
    if sort_field == "title":
        sort_field = "title.keyword"
    if sort_field == "date":
        sort_field = "published"

    return [
        {sort_field: {"order": sort_order}}
    ]