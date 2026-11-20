import matplotlib

# Render plots off-screen in tests: no windows, plt.show() returns immediately.
matplotlib.use("Agg")
