#!/bin/bash

# Définir le fichier de changelog
CHANGELOG_FILE="changelog.txt"

# Commencer avec un en-tête pour le changelog
echo "Changelog" > $CHANGELOG_FILE
echo "=========" >> $CHANGELOG_FILE

# Obtenir la liste des tags, les plus récents en premier
#tags=$(git tag --sort=-creatordate)

# Obtenir la liste des tags, les plus récents en premier
unsorted_tags=$(git tag) # Obtenir la liste des tags
declare -A tag_dates # Créer un tableau associatif pour stocker les dates des commits
for tag in $unsorted_tags; do # Parcourir chaque tag et obtenir la date du commit associé
  commit_date=$(git log -1 --format=%ai "$tag")
  tag_dates["$tag"]=$commit_date
done
tags=$(for tag in "${!tag_dates[@]}"; do echo "$tag ${tag_dates[$tag]}"; done | sort -k2 -r | awk '{print $1}')

# Si aucun tag n'existe, générer le changelog à partir de tous les commits
if [ -z "$tags" ]; then
  echo "" >> $CHANGELOG_FILE
  echo "Version initiale" >> $CHANGELOG_FILE
  echo "" >> $CHANGELOG_FILE
  git log --pretty=format:"- %s" >> $CHANGELOG_FILE
else
  # Parcourir chaque tag
  for tag in $tags; do
    echo "" >> $CHANGELOG_FILE
    # Obtenir la date du tag
    tag_date=$(git log -1 --format=%ai $tag)
    echo "## $tag - Date: $tag_date" >> $CHANGELOG_FILE
    echo "" >> $CHANGELOG_FILE

    # Si ce n'est pas le premier tag, comparer avec le tag précédent
    previous_tag=$(git describe --tags --abbrev=0 $tag^ 2>/dev/null)
    if [ -n "$previous_tag" ]; then
      git log $previous_tag..$tag --invert-grep --grep="^Version" --pretty=format:"- %s" >> $CHANGELOG_FILE
    else
      # Si c'est le premier tag, obtenir tous les commits jusqu'à ce tag
      git log $tag --invert-grep --grep="^Version" --pretty=format:"- %s" >> $CHANGELOG_FILE
    fi

    echo "" >> $CHANGELOG_FILE
  done
fi

echo "Changelog généré dans $CHANGELOG_FILE"
