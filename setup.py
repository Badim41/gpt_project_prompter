from setuptools import setup, find_packages

setup(
    name='gpt_project_prompter',
    version='1.3',
    packages=find_packages(),
    install_requires=[
        'network_tools @ git+https://github.com/Badim41/network_tools.git',
        'convert_gpt_answer @ git+https://github.com/Badim41/convert_gpt_answer.git',
    ],
)
