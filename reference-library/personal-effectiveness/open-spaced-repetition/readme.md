---
title: "FSRS4Anki"
source: Open Spaced Repetition (FSRS)
source_url: https://github.com/open-spaced-repetition/fsrs4anki
licence: MIT
domain: personal-effectiveness
subdomain: open-spaced-repetition
date_added: 2026-04-25
---

<p align="center">
  [
    
  ](https://github.com/open-spaced-repetition/fsrs4anki/wiki)
</p>


# FSRS4Anki

_✨ A modern spaced-repetition scheduler for Anki based on the [Free Spaced Repetition Scheduler algorithm](https://github.com/open-spaced-repetition/awesome-fsrs/wiki/The-Algorithm) ✨_  


<p align="center">
  [
    
  ](https://raw.githubusercontent.com/open-spaced-repetition/fsrs4anki/main/LICENSE)
  [
    
  ](https://github.com/open-spaced-repetition/fsrs4anki/releases/latest)
</p>

# Table of contents

- [Introduction](#introduction)
- [How to Get Started?](#how-to-get-started)
- [Add-on Compatibility](#add-on-compatibility)
- [Contribute](#contribute)
  - [Contributors](#contributors)
- [Developer Resources](#developer-resources)
- [Stargazers Over Time](#stargazers-over-time)
- [Acknowledgements](#acknowledgements)

# Introduction

FSRS4Anki (Free Spaced Repetition Scheduler for Anki) consists of two main parts: the scheduler and the optimizer.

- The scheduler replaces Anki's built-in scheduler and schedules the cards according to the FSRS algorithm.
- The optimizer uses machine learning to learn your memory patterns and finds parameters that best fit your review history. For details about the working of the optimizer, please read [the mechanism of optimization](https://github.com/open-spaced-repetition/awesome-fsrs/wiki/The-mechanism-of-optimization).

For details about the FSRS algorithm, please read [the algorithm](https://github.com/open-spaced-repetition/awesome-fsrs/wiki/The-Algorithm). If you are interested, you can also read my papers:
- [A Stochastic Shortest Path Algorithm for Optimizing Spaced Repetition Scheduling](https://dl.acm.org/doi/10.1145/3534678.3539081?cid=99660547150) (free access) [[中文版](https://memodocs.maimemo.com/docs/2022_KDD)], and
- [Optimizing Spaced Repetition Schedule by Capturing the Dynamics of Memory](https://drive.google.com/file/u/0/d/1riJbkH39JB71Wj0AzESTngUM0LaeoD2l/view) (Google Scholar) [[中文版](https://memodocs.maimemo.com/docs/2023_TKDE)].

FSRS Helper is an Anki add-on that complements the FSRS4Anki Scheduler. You can read about it here: https://github.com/open-spaced-repetition/fsrs4anki-helper

# How to Get Started?

If you are using Anki 23.10 or newer, refer to this section of [the Anki manual](https://docs.ankiweb.net/deck-options.html#fsrs).

If you are using an older version of Anki, refer to [this tutorial](https://github.com/open-spaced-repetition/fsrs4anki/blob/main/docs/tutorial2.md).

Note that setting up FSRS is much easier in Anki 23.10 or newer.

# Add-on Compatibility

Some add-ons can cause conflicts with FSRS. As a general rule of thumb, if an add-on affects a card's intervals, it shouldn't be used with FSRS.

| Add-on                                                       | Compatible? | Comment |
| ------------------------------------------------------------ |-------------------| ------- |
| [Review Heatmap](https://ankiweb.net/shared/info/1771074083) | Yes :white_check_mark: | Doesn't affect anything FSRS-related. |
| [Advanced Browser](https://ankiweb.net/shared/info/874215009) | Yes :white_check_mark: | Please use the latest version. |
| [Advanced Review Bottom Bar](https://ankiweb.net/shared/info/1136455830) | Yes :white_check_mark: | Please use the latest version. |
| [The KING of Button Add-ons](https://ankiweb.net/shared/info/374005964) | Yes :white_check_mark: | Please use the latest version. |
| [Pass/Fail](https://ankiweb.net/shared/info/876946123) | Yes :white_check_mark: | `Pass` is the equivalent of `Good`, `Fail` is the equivalent of `Again.` |
| [AJT Card Management](https://ankiweb.net/shared/info/1021636467) | Yes :white_check_mark: | Compatible with Anki 23.12 and newer. |
| [Incremental Reading v4.11.3 (unofficial clone)](https://ankiweb.net/shared/info/999215520) | Unsure :question: | If you are using the standalone version of FSRS, it shows the interval given by Anki's built-in scheduler, not the custom scheduler. This add-on is technically compatible with built-in FSRS, but FSRS was not designed for incremental reading, and FSRS settings do not apply to IR cards because they work in a different way compared to other card types. |
| [Delay siblings](https://ankiweb.net/shared/info/1369579727) | No :x:| Delay siblings will modify the intervals given by FSRS. However, the FSRS Helper add-on has a similar feature that works better with FSRS. Please use the FSRS Helper add-on instead. |
| [Auto Ease Factor](https://ankiweb.net/shared/info/1672712021) | No :x: | The Ease Factor is no longer relevant when FSRS is enabled, therefore you won't benefit from using this add-on. |
| [autoLapseNewInterval](https://ankiweb.net/shared/info/372281481) |No :x:| The `New Interval` setting is no longer relevant when FSRS is enabled, therefore you won't benefit from using this add-on. |
| [Straight Reward](https://ankiweb.net/shared/info/957961234) | No :x: | The Ease Factor is no longer relevant when FSRS is enabled, therefore you won't benefit from using this add-on. |

Let me know via [issues](https://github.com/open-spaced-repetition/fsrs4anki/issues) if you want me to check compatibility between FSRS and some add-on.

# Contribute

You can contribute to FSRS4Anki by beta testing, submitting code, or sharing your data. If you want to share your data with me, please fill out this form: https://forms.gle/KaojsBbhMCytaA7h8

## Contributors


<table>
  <tbody>
    <tr>
      <td align="center" valign="top" width="14.28%">[<sub>Expertium</sub>](https://github.com/Expertium)[⚠️](https://github.com/open-spaced-repetition/fsrs4anki/commits?author=Expertium) [📖](https://github.com/open-spaced-repetition/fsrs4anki/commits?author=Expertium) [🔣](#data-Expertium) [🤔](#ideas-Expertium) [🐛](https://github.com/open-spaced-repetition/fsrs4anki/issues?q=author%3AExpertium)</td>
      <td align="center" valign="top" width="14.28%">[<sub>user1823</sub>](https://github.com/user1823)[⚠️](https://github.com/open-spaced-repetition/fsrs4anki/commits?author=user1823) [📖](https://github.com/open-spaced-repetition/fsrs4anki/commits?author=user1823) [🔣](#data-user1823) [🤔](#ideas-user1823) [🐛](https://github.com/open-spaced-repetition/fsrs4anki/issues?q=author%3Auser1823)</td>
      <td align="center" valign="top" width="14.28%">[<sub>Christos Longros</sub>](http://chrislongros.com)[🔣](#data-chrislongros) [🖋](#content-chrislongros)</td>
    </tr>
  </tbody>
</table>


# Developer Resources

If you're a developer considering using the FSRS algorithm in your own projects, we've curated some valuable resources for you. Check out the [Awesome FSRS](https://github.com/open-spaced-repetition/awesome-fsrs) repository, where you'll find:

- FSRS implementations in various programming languages
- Related papers and research
- Example applications using FSRS
- Other algorithms and resources related to spaced repetition systems

This carefully curated list will help you better understand FSRS and choose the right implementation for your project. We encourage you to explore these resources and consider contributing to the FSRS ecosystem.

# Research Resources

For those new to spaced repetition algorithms, we recommend starting with our comprehensive guide: [Spaced Repetition Algorithm: A Three-Day Journey from Novice to Expert](https://github.com/open-spaced-repetition/awesome-fsrs/wiki/Spaced-Repetition-Algorithm:-A-Three%E2%80%90Day-Journey-from-Novice-to-Expert)

Dive deeper into the academic foundations of FSRS and spaced repetition through our curated collection of [Datasets, Code & Research Papers](https://github.com/open-spaced-repetition/awesome-fsrs/wiki/Research-resources)

Explore our extensive collection of [Research Notebooks](https://github.com/open-spaced-repetition/awesome-fsrs/wiki/Notebooks) documenting detailed analyses and experiments with FSRS and spaced repetition algorithms

# Stargazers Over Time

[
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=open-spaced-repetition/fsrs4anki&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=open-spaced-repetition/fsrs4anki&type=Date" />
   
 </picture>
](https://star-history.com/#open-spaced-repetition/fsrs4anki&Date)

# Acknowledgements

A special thanks to [墨墨背单词 (MaiMemo)](https://www.maimemo.com/) for their support of FSRS development by allowing its research engineer, [Jarrett Ye](https://github.com/L-M-Sherlock), to dedicate part of his working hours to this open-source project. This greatly helps in the continuous improvement and maintenance of FSRS for the benefit of the entire community.

We would also like to extend our sincere gratitude to [Damien Elmes](https://github.com/dae) of [Ankitects](https://github.com/ankitects) for his invaluable technical support, and to the AnkiWeb users for the review history dataset. Without their collective contribution, FSRS would not have achieved its current level of popularity and influence.
