from setuptools import setup, find_namespace_packages
setup(
    name="wisent-optimizer",
    version="0.1.1",
    author="Lukasz Bartoszcze and the Wisent Team",
    author_email="lukasz.bartoszcze@wisent.ai",
    description="Optuna+hyperopt-based steering hyperparameter optimizer for wisent",
    url="https://github.com/wisent-ai/wisent-optimizer",
    packages=find_namespace_packages(include=["wisent", "wisent.*"]),
    python_requires=">=3.9",
    install_requires=["wisent>=0.10.0", "optuna>=3.0.0", "hyperopt"],
)
