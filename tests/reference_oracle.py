"""Independent scalar oracle: no OpenCV, SciPy, or production analysis imports.

Gaussian convolution is explicit; components use a Python flood fill. Goldens
are scientific expectations from this implementation, never copied from tasks.
"""

import math

import numpy as np


def reflect(i, n):
    while i < 0 or i >= n:
        i = -i if i < 0 else 2 * n - 2 - i
    return i


def reference(image, kernel=5, sigma=1.0, threshold=127, min_area=20, connectivity=8):
    h, w = image.shape
    radius = kernel // 2
    weights = [math.exp(-i * i / (2 * sigma * sigma)) for i in range(-radius, radius + 1)]
    total = sum(weights)
    weights = [x / total for x in weights]
    binary = np.zeros((h, w), dtype=bool)
    for y in range(h):
        for x in range(w):
            value = sum(
                weights[j + radius]
                * weights[i + radius]
                * int(image[reflect(y + j, h), reflect(x + i, w)])
                for j in range(-radius, radius + 1)
                for i in range(-radius, radius + 1)
            )
            binary[y, x] = int(value + 0.5) > threshold
    visited = set()
    mask = np.zeros_like(binary)
    components = []
    directions = [(0, 1), (0, -1), (1, 0), (-1, 0)]
    if connectivity == 8:
        directions += [(1, 1), (1, -1), (-1, 1), (-1, -1)]
    for y in range(h):
        for x in range(w):
            if not binary[y, x] or (y, x) in visited:
                continue
            stack, pixels = [(y, x)], []
            visited.add((y, x))
            while stack:
                yy, xx = stack.pop()
                pixels.append((yy, xx))
                for dy, dx in directions:
                    point = (yy + dy, xx + dx)
                    if (
                        0 <= point[0] < h
                        and 0 <= point[1] < w
                        and binary[point]
                        and point not in visited
                    ):
                        visited.add(point)
                        stack.append(point)
            if len(pixels) >= min_area:
                for point in pixels:
                    mask[point] = True
                components.append(pixels)
    return mask, components
