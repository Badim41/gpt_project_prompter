from setuptools import setup, find_packages

setup(
    name='gpt_project_prompter',
    version='1.2',
    packages=find_packages(),
    install_requires=[
        'network-tools @ git+https://github.com/Badim41/network_tools.git',
    ],
)
