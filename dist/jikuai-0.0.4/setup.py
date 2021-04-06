import setuptools

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setuptools.setup(
    name="jikuai", # Replace with your own username
    version="0.0.4",
    author="Skywalk",
    author_email="skywalk163@vip.qq.com",
    description="A small tools for AI",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/skywalk163/jikuai",
    project_urls={
        "Bug Tracker": "https://github.com/skywalk163/jikuai/issues",
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    package_dir={"": "src"},
    packages=setuptools.find_packages(where="src"),
    python_requires=">=3.6",
)