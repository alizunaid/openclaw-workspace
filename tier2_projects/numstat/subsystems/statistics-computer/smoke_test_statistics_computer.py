import sys
from collections import defaultdict

def compute_statistics(number_stream):
    state = {
        'count': 0,
        'sum': 0,
        'min': float('inf'),
        'max': float('-inf')
    }
    
    for number in number_stream:
        try:
            num = float(number)
        except ValueError:
            print(f"Warning: '{number}' is not a valid number and will be skipped.", file=sys.stderr)
            continue
        
        state['count'] += 1
        state['sum'] += num
        if num < state['min']:
            state['min'] = num
        if num > state['max']:
            state['max'] = num
    
    mean = state['sum'] / state['count'] if state['count'] > 0 else float('nan')
    
    return {
        'count': state['count'],
        'sum': state['sum'],
        'mean': mean,
        'min': state['min'],
        'max': state['max']
    }

def main():
    if len(sys.argv) != 1:
        print("Usage: python3 statistics_computer.py", file=sys.stderr)
        sys.exit(1)
    
    # No-op self-check when invoked with no arguments
    print("statistics-computer: ready to compute summary statistics from a stream of numbers.")
    sys.exit(0)

if __name__ == "__main__":
    main()